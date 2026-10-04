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
 2. deja el asiento `storage.restauracion` en `public.audit_log` (tenant de sistema)
    con el SHA-256 del mapa, los recuentos y las versiones previas NO restauradas.

Si el asiento falla, la actualización se revierte (auditoría atómica con la
escritura). Entrada: el mapa TSV por stdin (`OBJETO<TAB>bucket/clave<TAB>vieja<TAB>nueva`).

    python -m app.ops.registrar_restauracion --respaldo 20261003_020000 \
        --previas-no-restauradas 0 < mapa-version-id.tsv
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
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
    previas_no_restauradas: int = 0,
) -> dict[str, int | str]:
    """Aplica el mapa y asienta el acta en una sola transacción. Devuelve el resumen."""
    if _audit is None:
        raise RuntimeError("orpycamcp_common.audit no está disponible: no se registra sin traza")
    remapeos = parsear_mapa(mapa_texto)
    actualizadas: Counter[str] = Counter()
    sin_fila = 0
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
            res = await conn.execute(
                f'UPDATE "{schema}".preservacion_worm_objeto SET version_id = $1 '
                "WHERE bucket = $2 AND object_key = $3 AND version_id = $4",
                r.nueva,
                r.bucket,
                r.clave,
                r.vieja,
            )
            if res.endswith(" 1"):
                actualizadas[m.group(1)] += 1
            else:
                sin_fila += 1
        resumen: dict[str, int | str] = {
            "respaldo": respaldo,
            "mapa_sha256": hashlib.sha256(mapa_texto.encode("utf-8")).hexdigest(),
            "objetos_en_mapa": len(remapeos),
            "filas_worm_actualizadas": sum(actualizadas.values()),
            "sin_fila_worm": sin_fila,
            "previas_compliance_no_restauradas": previas_no_restauradas,
        }
        await _audit.append(
            conn,
            tenant_slug=None,  # tenant de sistema: la restauración es de toda la instalación
            service="auth-service",
            action="storage.restauracion",
            object_type="respaldo",
            object_ref=respaldo,
            actor=_ACTOR,
            payload={**resumen, "por_tenant": dict(actualizadas)},
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
    p.add_argument("--previas-no-restauradas", type=int, default=0)
    return asyncio.run(_main(p.parse_args(sys.argv[1:] if argv is None else argv)))


if __name__ == "__main__":
    sys.exit(main())
