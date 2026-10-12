# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Acta inmutable de una restauración del almacén (T14, hallazgo de auditoría).

`infra/restore.sh` reponía la retención de cada objeto y escribía el mapa
`version_id viejo -> nuevo` en un fichero del host: ni asiento en `audit_log` ni
actualización de `preservacion_worm_objeto.version_id`, que tras restaurar apuntaba
a versiones que ya no existían (ADR-027 exige el acta; Ley 594 art. 4, trazabilidad;
PREMIS, evento de migración/replicación).

Este módulo, en UNA transacción:

 1. actualiza `preservacion_worm_objeto.version_id` (schema `tenant_{slug}` derivado
    del bucket `orpycamcp-{slug}-documents`) desde el mapa, solo donde el
    `version_id` guardado es exactamente el viejo;
 2. inserta un evento PREMIS `REPLICA` (`detalle.motivo='restauracion'`) por objeto
    remapeado en `preservacion_evento` (si la tabla falta, todo se revierte);
 3. deja el asiento `storage.restauracion` en `public.audit_log` (tenant de sistema)
    con el SHA-256 del mapa, los recuentos y las versiones previas NO restauradas;
 4. (ARC-M4) atribuye el acto a una PERSONA (`--operador`, obligatorio) y declara la
    VENTANA DE TRAZA PERDIDA: `restore.sh` captura, ANTES de `pg_restore`, el extremo
    de `audit_log` por tenant (conteo, id y hash del último asiento) y lo envía en el
    mismo stdin; aquí se compara con el extremo ya restaurado. Restaurar un respaldo
    más viejo que el estado vivo suprime asientos legales: el acta dice cuáles
    (`(desde_id, hasta_id]`) y cuántos, no lo calla.

Si el asiento falla, la actualización se revierte (auditoría atómica con la
escritura). Entrada: el mapa TSV por stdin (`OBJETO<TAB>bucket/clave<TAB>vieja<TAB>nueva`)
precedido de la captura previa (`CAPTURA_PREVIA<TAB>OK` y una línea
`EXTREMO_PREVIO<TAB>slug<TAB>asientos<TAB>ultimo_id<TAB>ultimo_hash` por tenant). Sin
el marcador no hay acta: una captura ausente no puede confundirse con «no había nada».

    python -m app.ops.registrar_restauracion --respaldo 20261003_020000 \
        --operador "Ana Pérez" --previas-no-restauradas 0 < captura+mapa.tsv
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
import sys
from collections import Counter
from dataclasses import dataclass

import asyncpg
from orpycamcp_common.tenant import slug_valido, tenant_schema

from app.core.config import settings

try:
    from orpycamcp_common import audit as _audit
except Exception:  # noqa: BLE001
    _audit = None  # type: ignore[assignment]

# El slug capturado se valida con el validador único (`orpycamcp_common.tenant`).
_BUCKET_RE = re.compile(r"^orpycamcp-([a-z0-9-]+)-documents$")
_ACTOR = "system:ops-restauracion"


@dataclass(frozen=True, slots=True)
class Extremo:
    """Extremo de la cadena de un tenant en `audit_log`."""

    slug: str
    asientos: int
    ultimo_id: int | None
    ultimo_hash: str | None


def parsear_extremos_previos(texto: str) -> list[Extremo]:
    """Extremos capturados ANTES de `pg_restore`. Exige el marcador `CAPTURA_PREVIA OK`."""
    capturada = False
    out: list[Extremo] = []
    for n, linea in enumerate(texto.splitlines(), start=1):
        if linea == "CAPTURA_PREVIA\tOK":
            capturada = True
        elif linea.startswith("EXTREMO_PREVIO\t"):
            campos = linea.split("\t")
            if len(campos) != 5:
                raise ValueError(f"línea {n}: EXTREMO_PREVIO malformado")
            try:
                out.append(Extremo(campos[1], int(campos[2]), int(campos[3]), campos[4]))
            except ValueError as exc:
                raise ValueError(f"línea {n}: EXTREMO_PREVIO con número inválido ({exc})") from exc
    if not capturada:
        raise ValueError(
            "falta la captura previa del extremo de audit_log (CAPTURA_PREVIA OK): "
            "sin ella no se puede declarar la ventana de traza perdida"
        )
    return out


def validar_operador(operador: str) -> str:
    """Una persona identificable: no vacío, sin caracteres de control, mínimo 3."""
    limpio = (operador or "").strip()
    if len(limpio) < 3 or any(ord(c) < 32 for c in limpio):
        raise ValueError("--operador es obligatorio: nombre de la persona que ejecuta la restauración")
    return limpio


async def _extremo_restaurado(conn: asyncpg.Connection, slug: str) -> tuple[int, int | None, str | None]:
    fila = await conn.fetchrow(
        "SELECT count(*) AS n, max(id) AS ultimo_id FROM public.audit_log WHERE tenant_slug = $1", slug
    )
    assert fila is not None
    ultimo_id = fila["ultimo_id"]
    hash_ = None
    if ultimo_id is not None:
        hash_ = await conn.fetchval(
            "SELECT hash FROM public.audit_log WHERE id = $1 AND tenant_slug = $2", ultimo_id, slug
        )
    return int(fila["n"]), ultimo_id, hash_


async def ventanas_de_traza_perdida(conn: asyncpg.Connection, previos: list[Extremo]) -> list[dict[str, object]]:
    """Por tenant con asientos que la restauración dejó fuera: `(desde_id, hasta_id]`.

    `hasta_id` es el último asiento que existía ANTES de restaurar (con su hash, como
    prueba); `desde_id` es el último que quedó. Solo se listan los tenants con pérdida.
    """
    ventanas: list[dict[str, object]] = []
    for p in previos:
        if p.ultimo_id is None:
            continue
        n, ultimo_id, hash_ = await _extremo_restaurado(conn, p.slug)
        if ultimo_id is not None and ultimo_id >= p.ultimo_id and n >= p.asientos:
            continue
        ventanas.append(
            {
                "tenant_slug": p.slug,
                "desde_id": ultimo_id or 0,
                "desde_hash": hash_,
                "hasta_id": p.ultimo_id,
                "hasta_hash": p.ultimo_hash,
                "asientos_previos": p.asientos,
                "asientos_restaurados": n,
                "asientos_perdidos": max(p.asientos - n, 0),
            }
        )
    return ventanas


@dataclass(frozen=True, slots=True)
class Remapeo:
    bucket: str
    clave: str
    vieja: str
    nueva: str


def parsear_mapa(texto: str) -> list[Remapeo]:
    out: list[Remapeo] = []
    for n, linea in enumerate(texto.splitlines(), start=1):
        if not linea.startswith("OBJETO\t"):
            continue
        campos = linea.split("\t")
        if len(campos) != 4 or "/" not in campos[1]:
            raise ValueError(f"línea {n} del mapa: formato inválido")
        bucket, clave = campos[1].split("/", 1)
        out.append(Remapeo(bucket, clave, campos[2], campos[3]))
    return out


async def registrar(
    conn: asyncpg.Connection,
    *,
    mapa_texto: str,
    respaldo: str,
    operador: str,
    previas_no_restauradas: int = 0,
) -> dict[str, object]:
    """Aplica el mapa y asienta el acta en una sola transacción. Devuelve el resumen."""
    if _audit is None:
        raise RuntimeError("orpycamcp_common.audit no está disponible: no se registra sin traza")
    operador = validar_operador(operador)
    previos = parsear_extremos_previos(mapa_texto)
    remapeos = parsear_mapa(mapa_texto)
    actualizadas: Counter[str] = Counter()
    sin_fila = 0
    eventos = 0
    async with conn.transaction():
        for r in remapeos:
            m = _BUCKET_RE.match(r.bucket)
            if not m or not slug_valido(m.group(1)) or r.vieja in ("", "-") or r.nueva in ("", "-") or r.vieja == r.nueva:
                sin_fila += 1
                continue
            schema = tenant_schema(m.group(1))
            # El nombre de schema no admite `$1`: sale de un slug validado por regex.
            if await conn.fetchval("SELECT to_regclass($1)", f'"{schema}".preservacion_worm_objeto') is None:
                sin_fila += 1
                continue
            filas = await conn.fetch(
                f'UPDATE "{schema}".preservacion_worm_objeto SET version_id = $1 '
                "WHERE bucket = $2 AND object_key = $3 AND version_id = $4 "
                "RETURNING expediente_id, tipo, sha256",
                r.nueva,
                r.bucket,
                r.clave,
                r.vieja,
            )
            if not filas:
                sin_fila += 1
                continue
            for fila in filas:
                actualizadas[m.group(1)] += 1
                # PREMIS (replicación): el objeto remapeado deja su evento en la MISMA
                # transacción. Si falta `preservacion_evento` en el schema, el INSERT
                # falla y TODO se revierte (fail-closed): un remapeo sin evento no existe.
                # Este módulo NO lee el objeto restaurado (auth-service no tiene cliente ni
                # credenciales del almacén), así que NO declara `hash_after`: copiar el
                # SHA-256 registrado como «después» afirmaría una medición que no ocurrió.
                # `hash_before` es el valor registrado; la fixity queda «no_verificada».
                await conn.execute(
                    f'INSERT INTO "{schema}".preservacion_evento '
                    "(object_ref, tipo, resultado, hash_before, hash_after, detalle, agente) "
                    "VALUES ($1, 'REPLICA', 'ok', $2, NULL, $3::jsonb, $4)",
                    str(fila["expediente_id"]),
                    fila["sha256"],
                    json.dumps(
                        {
                            "evento": "replication",
                            "motivo": "restauracion",
                            "respaldo": respaldo,
                            "bucket": r.bucket,
                            "object_key": r.clave,
                            "version_vieja": r.vieja,
                            "version_nueva": r.nueva,
                            "artefacto": fila["tipo"],
                            "fixity": "no_verificada",
                        }
                    ),
                    _ACTOR,
                )
                eventos += 1
        ventanas = await ventanas_de_traza_perdida(conn, previos)
        resumen: dict[str, object] = {
            "respaldo": respaldo,
            "operador": operador,
            "tenants_con_traza_perdida": len(ventanas),
            "asientos_perdidos": sum(int(v["asientos_perdidos"]) for v in ventanas),  # type: ignore[call-overload]
            "mapa_sha256": hashlib.sha256(mapa_texto.encode("utf-8")).hexdigest(),
            "objetos_en_mapa": len(remapeos),
            "filas_worm_actualizadas": sum(actualizadas.values()),
            "sin_fila_worm": sin_fila,
            "eventos_premis": eventos,
            "previas_compliance_no_restauradas": previas_no_restauradas,
        }
        await _audit.append(
            conn,
            tenant_slug=None,  # tenant de sistema: la restauración es de toda la instalación
            service="auth-service",
            action="storage.restauracion",
            object_type="respaldo",
            object_ref=respaldo,
            actor=f"operador:{operador}",
            payload={**resumen, "por_tenant": dict(actualizadas), "ventanas_de_traza_perdida": ventanas},
            canal="system",
        )
    return resumen


async def _main(args: argparse.Namespace) -> int:
    texto = sys.stdin.read()
    conn = await asyncpg.connect(settings.database_url_asyncpg)
    try:
        resumen = await registrar(
            conn,
            mapa_texto=texto,
            respaldo=args.respaldo,
            operador=args.operador,
            previas_no_restauradas=args.previas_no_restauradas,
        )
    except Exception as exc:  # noqa: BLE001
        print(f"✗ no se registró la restauración: {exc}", file=sys.stderr)
        return 1
    finally:
        await conn.close()
    print(f"   acta registrada: {resumen}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Registra la restauración del almacén en audit_log.")
    p.add_argument("--respaldo", required=True, help="nombre del directorio de respaldo restaurado")
    p.add_argument("--operador", required=True, help="persona que ejecuta la restauración (obligatorio)")
    p.add_argument("--previas-no-restauradas", type=int, default=0)
    return asyncio.run(_main(p.parse_args(sys.argv[1:] if argv is None else argv)))


if __name__ == "__main__":
    sys.exit(main())
