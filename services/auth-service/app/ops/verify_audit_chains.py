# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Verificación programada de la cadena de auditoría contra su ancla (AREA-03-02).

Hasta ahora `verify_chain` solo corría bajo demanda (`GET /api/v1/audit/verify`) o
dentro de un heredoc de `infra/restore.sh` — y ese heredoc desempaquetaba el
resultado como una tupla `(ok, fila)` cuando `verify_chain` devuelve un
`ResultadoCadena`: la verificación de la restauración reventaba con `TypeError`.
Peor: `infra/backup.sh` ANCLABA el extremo de la cadena (conteo + hash) sin
verificarla, así que podía sellar como «extremo de confianza» una cadena YA rota.

Este módulo es el único sitio que sabe verificar y comparar con el ancla; lo
invocan `backup.sh` (ANTES de anclar) y `restore.sh` (en lugar del heredoc), y
queda listo para un cron diario (ver `docs/es/deployment.md`).

Canal de OPS, no endpoint HTTP (familia de `purge_revoked_signing_keys`):

    # verifica y compara con el ancla acumulativa (TSV por stdin):
    python -m app.ops.verify_audit_chains --anclas - < anclas-auditoria.tsv
    # además emite por stdout las líneas del ancla NUEVO (solo si TODO está bien):
    python -m app.ops.verify_audit_chains --anclas - --emitir-ancla 20261002-0300 < ...
    # solo estos tenants (p. ej. en pruebas):  --slug demo --slug __system__
    # con roturas HISTÓRICAS declaradas (acta aprobada por el responsable):
    #   --roturas-declaradas roturas.tsv   (líneas `ROTURA<TAB>slug<TAB>id[<TAB>nota]`;
    #   las mismas líneas también se aceptan mezcladas en el stdin de --anclas)

Por stdout salen SOLO las líneas del ancla (para que el shell las anexe al fichero
acumulativo SIN montar nada en escritura dentro del contenedor, regla 3); todo lo
demás va a stderr.

INVARIANTES (fail-closed):
 - Recorre `public.tenants` MÁS todo `tenant_slug` que figure en `audit_log`
   (incluido el del sistema, que no tiene fila en `tenants`), MÁS los slugs del
   ancla: un tenant cuyo extremo estaba anclado y ya no tiene asientos es un
   retroceso, no «nada que verificar».
 - Por cada slug: la cadena recalculada debe ser íntegra; el conteo no puede bajar
   respecto al último ancla; y el hash del asiento `ultimo_id` anclado no puede
   haber cambiado ni haber desaparecido.
 - `audit_log` solo admite anexar (Ley 594/2000): una rotura histórica no se repara
   NUNCA. Para que una sola fila mala no inutilice los respaldos para siempre, el
   responsable puede DECLARARLA (línea base `--roturas-declaradas`). Solo cuentan
   las roturas fuera de esa lista: una rotura NUEVA, una rotura no enumerable
   (tope de `verify_chain`), un retroceso del conteo o un cambio del hash anclado
   siguen fallando. Una rotura declarada se informa en cada corrida, no se calla.
 - Código de salida 0 = todo bien; 1 = rotura, retroceso o ancla ilegible; 2 = uso.
   Si algo falla NO se emite ancla: sellar una cadena rota como extremo de
   confianza es justo lo que esto impide.
 - Solo LEE `audit_log`. Al terminar ANOTA el resultado agregado en
   `public.audit_chain_runs` (C18, ADR-041), que NO es un asiento de la cadena: no
   mueve el extremo que `backup.sh` ancla en la misma corrida. La anotación es
   best-effort: si falla, aviso a stderr y el código de salida NO cambia (un
   respaldo no se rompe por una métrica; la ausencia la delata la alarma de
   antigüedad). Una corrida con `--slug` o `--excluir-prefijo` se anota `parcial`
   y `/metrics` NO la cuenta como verificación. `--origen` dice quién corrió
   (`backup`, `restore`, `cron`, `manual`).

`--excluir-prefijo` existe para DESARROLLO (los tests de integración históricos
dejaron en `audit_log`, que es append-only, filas `itest-*` con hash de juguete que
nunca se podrán borrar). En producción no se usa; backup.sh solo lo pasa si el
operador define `BACKUP_AUDIT_EXCLUIR_PREFIJOS`.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from dataclasses import dataclass
from datetime import UTC, datetime

import asyncpg

from app.core.config import settings

try:
    from orpycamcp_common import audit as _audit
except Exception:  # noqa: BLE001  (sin librería no hay verificación: se falla, abajo)
    _audit = None  # type: ignore[assignment]

_CABECERA = "fecha\ttenant_slug\tasientos\tultimo_id\tultimo_hash"


@dataclass(frozen=True, slots=True)
class Ancla:
    fecha: str
    slug: str
    asientos: int
    ultimo_id: int
    ultimo_hash: str


class AnclaIlegible(ValueError):
    """Una línea del ancla no se puede interpretar: no se adivina, se falla."""


_ROTURA = "ROTURA"


def parsear_roturas_declaradas(texto: str) -> dict[str, frozenset[int]]:
    """`ROTURA<TAB>slug<TAB>id[<TAB>nota]` -> {slug: ids}. Las demás líneas se ignoran."""
    declaradas: dict[str, set[int]] = {}
    for n, linea in enumerate(texto.splitlines(), start=1):
        if not linea.startswith(_ROTURA + "\t"):
            continue
        campos = linea.split("\t")
        if len(campos) < 3:
            raise AnclaIlegible(f"línea {n}: ROTURA declarada sin slug o sin id")
        try:
            declaradas.setdefault(campos[1], set()).add(int(campos[2]))
        except ValueError as exc:
            raise AnclaIlegible(f"línea {n}: id de rotura inválido ({exc})") from exc
    return {k: frozenset(v) for k, v in declaradas.items()}


def parsear_anclas(texto: str) -> dict[str, Ancla]:
    """El ÚLTIMO ancla de cada slug (el extremo más reciente registrado)."""
    ultimos: dict[str, Ancla] = {}
    for n, linea in enumerate(texto.splitlines(), start=1):
        if not linea.strip() or linea.strip() == _CABECERA or linea.startswith(_ROTURA + "\t"):
            continue
        campos = linea.split("\t")
        if len(campos) != 5:
            raise AnclaIlegible(f"línea {n} del ancla: se esperaban 5 columnas, hay {len(campos)}")
        fecha, slug, asientos, ultimo_id, ultimo_hash = campos
        try:
            ultimos[slug] = Ancla(fecha, slug, int(asientos), int(ultimo_id), ultimo_hash)
        except ValueError as exc:
            raise AnclaIlegible(f"línea {n} del ancla: número inválido ({exc})") from exc
    return ultimos


async def _slugs(conn: asyncpg.Connection) -> list[str]:
    slugs = {r["slug"] for r in await conn.fetch("SELECT slug FROM public.tenants")}
    slugs |= {
        r["tenant_slug"] for r in await conn.fetch("SELECT DISTINCT tenant_slug FROM public.audit_log")
    }
    return sorted(slugs)


async def _extremo(conn: asyncpg.Connection, slug: str) -> tuple[int, int | None, str | None]:
    """(asientos, ultimo_id, ultimo_hash) del tenant."""
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


async def verificar(
    conn: asyncpg.Connection,
    *,
    anclas: dict[str, Ancla],
    slugs: list[str] | None = None,
    excluir_prefijos: tuple[str, ...] = (),
    fecha_ancla: str | None = None,
    roturas_declaradas: dict[str, frozenset[int]] | None = None,
    permitir_objetivo_vacio: bool = False,
    estadisticas: dict[str, int] | None = None,
) -> tuple[int, list[str], list[str]]:
    """(código de salida, mensajes, líneas del ancla nuevo).

    Las líneas del ancla solo se devuelven si el código es 0.
    `roturas_declaradas` es la línea base aprobada: solo se tolera lo que está en ella.
    `estadisticas`, si se pasa, se rellena con los agregados de la corrida
    (`tenants`, `asientos`, `roturas_nuevas`, `roturas_declaradas`, `retrocesos`,
    `anclas_alteradas`) SOLO cuando se llegó a verificar cadenas; en las salidas
    tempranas queda vacío y no hay nada que anotar.
    """
    declaradas = roturas_declaradas or {}
    if _audit is None:
        return 1, ["✗ orpycamcp_common.audit no está disponible: no se puede verificar"], []
    objetivo = slugs if slugs is not None else sorted(set(await _slugs(conn)) | set(anclas))
    objetivo = [s for s in objetivo if not s.startswith(excluir_prefijos)] if excluir_prefijos else objetivo
    if not objetivo:
        # Un objetivo vacío NO es «todo bien»: un filtro mal tecleado (o una base
        # vacía por un restore fallido) verificaría cero cadenas y saldría en verde.
        filtrado = slugs is not None or bool(excluir_prefijos)
        if filtrado:
            return 2, ["✗ objetivo vacío tras filtro: no se verificó ninguna cadena"], []
        if not permitir_objetivo_vacio:
            return 2, [
                "✗ objetivo vacío: no hay tenants ni asientos que verificar "
                "(use --permitir-objetivo-vacio solo en una instalación nueva)"
            ], []
        return 0, ["   (sin tenants: nada que verificar, permitido explícitamente)"], []

    mensajes: list[str] = []
    nuevas: list[str] = []
    fallo = False
    est = estadisticas if estadisticas is not None else {}
    est.update(
        tenants=len(objetivo),
        asientos=0,
        roturas_nuevas=0,
        roturas_declaradas=0,
        retrocesos=0,
        anclas_alteradas=0,
    )
    for slug in objetivo:
        res = await _audit.verify_chain(conn, slug)
        conocidas = declaradas.get(slug, frozenset())
        nuevas_roturas = [r.id for r in res.roturas if r.id not in conocidas]
        est["roturas_nuevas"] += len(nuevas_roturas) + res.roturas_omitidas
        est["roturas_declaradas"] += len(res.roturas) - len(nuevas_roturas)
        if nuevas_roturas or res.roturas_omitidas:
            mensajes.append(
                f"   ✗ {slug}: CADENA ROTA — {res.total_roturas} roturas, "
                f"{len(nuevas_roturas) + res.roturas_omitidas} fuera de la línea base declarada "
                f"(primera en el asiento id={nuevas_roturas[0] if nuevas_roturas else res.primera_rota})"
            )
            fallo = True
            continue
        declaradas_vistas = len(res.roturas)
        asientos, ultimo_id, ultimo_hash = await _extremo(conn, slug)
        est["asientos"] += asientos
        previo = anclas.get(slug)
        if previo is not None:
            if asientos < previo.asientos:
                mensajes.append(
                    f"   ✗ {slug}: la traza RETROCEDIÓ — {asientos} asientos; el último ancla "
                    f"({previo.fecha}) decía {previo.asientos}"
                )
                est["retrocesos"] += 1
                fallo = True
                continue
            hash_anclado = await conn.fetchval(
                "SELECT hash FROM public.audit_log WHERE id = $1 AND tenant_slug = $2",
                previo.ultimo_id,
                slug,
            )
            if hash_anclado != previo.ultimo_hash:
                mensajes.append(
                    f"   ✗ {slug}: el asiento anclado id={previo.ultimo_id} "
                    f"{'cambió de hash' if hash_anclado else 'ya no existe'} desde {previo.fecha}"
                )
                est["anclas_alteradas"] += 1
                fallo = True
                continue
        if declaradas_vistas:
            mensajes.append(
                f"   ! {slug}: {declaradas_vistas} roturas HISTÓRICAS declaradas (línea base aprobada); "
                f"sin roturas nuevas ({asientos} asientos)"
            )
        else:
            mensajes.append(f"   ✓ {slug}: cadena íntegra ({asientos} asientos)")
        if fecha_ancla and ultimo_id is not None and ultimo_hash is not None:
            nuevas.append(f"{fecha_ancla}\t{slug}\t{asientos}\t{ultimo_id}\t{ultimo_hash}")
    if fallo:
        mensajes.append("   No se emite ancla: una cadena rota no se sella como extremo de confianza.")
        return 1, mensajes, []
    return 0, mensajes, nuevas


def _argumentos(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Verifica la cadena de audit_log contra su ancla.")
    p.add_argument("--anclas", default=None, help="fichero TSV del ancla acumulativo, o `-` para stdin")
    p.add_argument("--emitir-ancla", metavar="FECHA", default=None, help="imprime las líneas del ancla nuevo")
    p.add_argument("--roturas-declaradas", metavar="FICHERO", default=None,
                   help="línea base de roturas históricas aprobadas (`ROTURA<TAB>slug<TAB>id`)")
    p.add_argument("--slug", action="append", default=None, help="solo este tenant (repetible)")
    p.add_argument("--permitir-objetivo-vacio", action="store_true",
                   help="acepta salir en 0 cuando no hay tenants (instalación nueva); backup/restore no lo pasan")
    p.add_argument("--excluir-prefijo", action="append", default=[], help="SOLO desarrollo")
    p.add_argument("--origen", choices=ORIGENES, default="manual",
                   help="quién corre; se anota en audit_chain_runs (backup.sh/restore.sh lo pasan)")
    return p.parse_args(argv)


ORIGENES = ("backup", "restore", "cron", "manual")

_INSERT_CORRIDA = (
    "INSERT INTO public.audit_chain_runs (started_at, finished_at, alcance, origen, tenants, "
    "asientos, roturas_nuevas, roturas_declaradas, retrocesos, anclas_alteradas, codigo_salida) "
    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)"
)


async def registrar_corrida(
    conn: asyncpg.Connection,
    *,
    inicio: datetime,
    alcance: str,
    origen: str,
    estadisticas: dict[str, int],
    codigo_salida: int,
) -> bool:
    """Anota UNA fila en `audit_chain_runs`. Best-effort: nunca cambia el código de salida.

    `alcance` es `parcial` cuando se acotó el objetivo (`--slug`/`--excluir-prefijo`):
    una verificación parcial no es «la» verificación y `/metrics` no la cuenta. Sin
    estadísticas (salida temprana: objetivo vacío, ancla ilegible) no hay nada que anotar.
    """
    if not estadisticas:
        return False
    try:
        await conn.execute(
            _INSERT_CORRIDA,
            inicio,
            datetime.now(UTC),
            alcance,
            origen,
            estadisticas["tenants"],
            estadisticas["asientos"],
            estadisticas["roturas_nuevas"],
            estadisticas["roturas_declaradas"],
            estadisticas["retrocesos"],
            estadisticas["anclas_alteradas"],
            codigo_salida,
        )
    except Exception as exc:  # noqa: BLE001 - la métrica no rompe el respaldo
        print(
            f"! no se pudo anotar la corrida en audit_chain_runs ({type(exc).__name__}); "
            "la alarma de antigüedad lo delatará",
            file=sys.stderr,
        )
        return False
    return True


async def _main(args: argparse.Namespace) -> int:
    try:
        if args.anclas == "-":
            texto = sys.stdin.read()
        elif args.anclas:
            with open(args.anclas, encoding="utf-8") as fh:
                texto = fh.read()
        else:
            texto = ""
        anclas = parsear_anclas(texto)
        if args.roturas_declaradas:
            with open(args.roturas_declaradas, encoding="utf-8") as fh:
                texto_roturas = fh.read()
        else:
            texto_roturas = ""
        # Las líneas ROTURA también valen mezcladas en el stdin de las anclas
        # (backup.sh no monta ficheros dentro del contenedor, regla 3).
        declaradas = parsear_roturas_declaradas(texto + "\n" + texto_roturas)
    except (AnclaIlegible, OSError) as exc:
        print(f"✗ ancla ilegible: {exc}", file=sys.stderr)
        return 1

    inicio = datetime.now(UTC)
    estadisticas: dict[str, int] = {}
    alcance = "parcial" if (args.slug or args.excluir_prefijo) else "completo"
    conn = await asyncpg.connect(settings.database_url_asyncpg)
    try:
        codigo, mensajes, nuevas = await verificar(
            conn,
            anclas=anclas,
            slugs=args.slug,
            excluir_prefijos=tuple(args.excluir_prefijo),
            fecha_ancla=args.emitir_ancla,
            roturas_declaradas=declaradas,
            permitir_objetivo_vacio=args.permitir_objetivo_vacio,
            estadisticas=estadisticas,
        )
        await registrar_corrida(
            conn,
            inicio=inicio,
            alcance=alcance,
            origen=args.origen,
            estadisticas=estadisticas,
            codigo_salida=codigo,
        )
    finally:
        await conn.close()
    for m in mensajes:
        print(m, file=sys.stderr)
    for linea in nuevas:
        print(linea)
    return codigo


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(_main(_argumentos(sys.argv[1:] if argv is None else argv)))


if __name__ == "__main__":
    sys.exit(main())
