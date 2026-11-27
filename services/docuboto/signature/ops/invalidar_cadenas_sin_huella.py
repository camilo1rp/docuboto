# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Invalida, en lote, las cadenas ANTERIORES a la huella de contenido (AREA-04-09).

Una cadena sobre un `radicado`/`expediente` abierta antes de que el servidor
fijara la huella tiene `payload_sha256 IS NULL`: firmaba el identificador, no el
contenido. Hoy se rechaza al firmar (`409 cadena_sin_huella`), pero mientras
siga `pendiente` ocupa el índice único de cadena abierta
(`ux_firma_solicitud_objeto_abierta`): no se puede abrir la de reemplazo y los
turnos siguen en la bandeja de quien los tenía, pidiéndole firmar algo que ya no
puede firmarse. Este script las deja `invalidada` con
``invalidada_motivo = 'cadena_sin_huella'``, que sí libera el índice.

Canal de OPS, NO endpoint HTTP (acto masivo sobre todos los tenants):

    python -m app.ops.invalidar_cadenas_sin_huella --operador ANA --dry-run   # cuenta, no toca
    python -m app.ops.invalidar_cadenas_sin_huella --operador ANA             # todos los tenants
    python -m app.ops.invalidar_cadenas_sin_huella --operador ANA --tenant icetex

``--operador`` (``auth_users.username``) es OBLIGATORIO y debe existir y estar
activo en CADA tenant que se va a procesar: se comprueba ANTES de tocar nada y,
si falta en alguno, se aborta con salida != 0 sin escribir. Es la persona que
responde por el acto masivo: queda como actor (``auth_users.id``) de todos los
asientos. Cada tenant procesado deja además dos asientos
``firma.invalidacion_masiva_ejecutada`` (``fase`` = ``inicio`` y ``fin``, mismo
``corrida_id``) con los conteos y el indicador ``dry_run`` —también en dry-run:
mirar quedó registrado—.

INVARIANTES:
 - El asiento lo escribe la APLICACIÓN (``orpycamcp_common.audit.append``, cadena
   de hash), uno POR CADENA y en la MISMA transacción que su cambio de estado:
   nunca hay cadena invalidada sin asiento ni asiento sin cadena invalidada. Si
   la auditoría no está disponible se aborta (no se invalida sin traza).
 - Solo `pendiente` con huella NULL. Una `firmado` o `rechazada` es historia y no
   se toca; una `pendiente` con huella es una cadena vigente y tampoco.
 - Los turnos aún pendientes caen con la solicitud; los ya resueltos (firmados)
   se conservan intactos: son el registro de un acto que sí ocurrió.
 - Idempotente y reanudable: el ``UPDATE`` repite la condición (``estado =
   'pendiente' AND payload_sha256 IS NULL``), así que una segunda corrida, o
   una cadena resuelta por otra petición entre la lectura y la escritura, no
   encuentra nada que hacer. Exit-code != 0 si alguna cadena falla.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import uuid

import asyncpg

try:  # misma disponibilidad opcional que el resto del servicio
    from orpycamcp_common import audit as _audit

    _AUDIT_AVAILABLE = True
except Exception:  # noqa: BLE001
    _audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

_SERVICE = "signature-service"
MOTIVO = "cadena_sin_huella"
ACCION_CORRIDA = "firma.invalidacion_masiva_ejecutada"
#: Tipos cuya huella la fija hoy el servidor y que por tanto no pueden seguir
#: abiertos sin ella. (`borrador` nunca tuvo cadenas sin huella: su apertura
#: siempre la resolvió.)
TIPOS = ("radicado", "expediente")

_RAW_DSN = os.environ.get("DATABASE_URL", "")
_DSN = _RAW_DSN.replace("postgresql+asyncpg://", "postgresql://") if _RAW_DSN else ""


def _require_dsn() -> str:
    if not _DSN:
        raise RuntimeError(
            "DATABASE_URL no está definida: este script de operaciones necesita la "
            "cadena de conexión a PostgreSQL."
        )
    return _DSN


def slug_de(schema: str) -> str:
    """Inverso exacto de ``core.database.tenant_schema`` (`-` -> `_`)."""
    return schema[len("tenant_"):].replace("_", "-") if schema.startswith("tenant_") else schema


async def _tenant_schemas(conn: asyncpg.Connection) -> list[str]:
    rows = await conn.fetch(
        "SELECT schema_name FROM information_schema.schemata "
        "WHERE schema_name LIKE 'tenant\\_%' ESCAPE '\\' ORDER BY schema_name"
    )
    return [r["schema_name"] for r in rows]


async def resolver_operador(conn: asyncpg.Connection, *, schema: str, username: str) -> uuid.UUID | None:
    """``auth_users.id`` del operador ACTIVO en ``schema``; ``None`` si no existe."""
    await conn.execute(f'SET search_path = "{schema}"')
    if await conn.fetchval("SELECT to_regclass('auth_users')") is None:
        return None
    return await conn.fetchval(
        "SELECT id FROM auth_users WHERE upper(username) = upper($1) AND is_active",
        username,
    )


async def _asentar_corrida(
    conn: asyncpg.Connection,
    *,
    slug: str,
    operador_id: uuid.UUID,
    operador: str,
    corrida_id: uuid.UUID,
    fase: str,
    dry_run: bool,
    conteos: dict[str, int],
) -> None:
    await _audit.append(
        conn,
        tenant_slug=slug,
        service=_SERVICE,
        action=ACCION_CORRIDA,
        object_type="firma_solicitud",
        object_ref=str(corrida_id),
        actor=str(operador_id),
        payload={
            "fase": fase,
            "corrida_id": str(corrida_id),
            "operador": operador,
            "dry_run": dry_run,
            "motivo": MOTIVO,
            **conteos,
        },
        canal="system",
    )


async def invalidar_en_tenant(
    conn: asyncpg.Connection,
    *,
    schema: str,
    slug: str,
    dry_run: bool,
    operador_id: uuid.UUID,
    operador: str,
) -> tuple[int, int]:
    """Devuelve (invalidadas, fallidas) de UN tenant."""
    await conn.execute(f'SET search_path = "{schema}"')
    if await conn.fetchval("SELECT to_regclass('firma_solicitud')") is None:
        return (0, 0)  # el tenant aún no migró signature-service
    corrida_id = uuid.uuid4()
    ids = await conn.fetch(
        "SELECT id FROM firma_solicitud "
        "WHERE estado = 'pendiente' AND payload_sha256 IS NULL "
        "AND objeto_tipo = ANY($1::text[]) ORDER BY created_at",
        list(TIPOS),
    )

    async def asentar(fase: str, conteos: dict[str, int]) -> None:
        await _asentar_corrida(
            conn, slug=slug, operador_id=operador_id, operador=operador,
            corrida_id=corrida_id, fase=fase, dry_run=dry_run, conteos=conteos,
        )

    # Si el asiento de inicio no se puede escribir, no se toca nada.
    await asentar("inicio", {"candidatas": len(ids)})
    if dry_run:
        if ids:
            print(f"  [{slug}] {len(ids)} cadena(s) se invalidarían")
        await asentar("fin", {"candidatas": len(ids), "invalidadas": 0, "fallidas": 0})
        return (len(ids), 0)

    hechas = fallidas = 0
    for fila in ids:
        try:
            async with conn.transaction():
                await conn.execute(f'SET LOCAL search_path = "{schema}"')
                sol = await conn.fetchrow(
                    """
                    UPDATE firma_solicitud
                       SET estado = 'invalidada',
                           invalidada_at = now(),
                           invalidada_motivo = $2,
                           updated_at = now()
                     WHERE id = $1
                       AND estado = 'pendiente'
                       AND payload_sha256 IS NULL
                 RETURNING id, objeto_tipo, objeto_id, nivel_seguridad
                    """,
                    fila["id"],
                    MOTIVO,
                )
                if sol is None:
                    continue  # otra corrida o petición la resolvió: idempotente
                turnos = await conn.fetch(
                    "UPDATE firma_cadena SET estado = 'invalidada' "
                    "WHERE solicitud_id = $1 AND estado = 'pendiente' RETURNING id",
                    sol["id"],
                )
                await _audit.append(
                    conn,
                    tenant_slug=slug,
                    service=_SERVICE,
                    action="firma.cadena_invalidada",
                    object_type=sol["objeto_tipo"],
                    object_ref=sol["objeto_id"],
                    actor=str(operador_id),
                    payload={
                        "operador": operador,
                        "corrida_id": str(corrida_id),
                        "solicitud_id": str(sol["id"]),
                        "motivo": MOTIVO,
                        "nivel_seguridad": sol["nivel_seguridad"],
                        "turnos_invalidados": len(turnos),
                    },
                    canal="system",
                )
            hechas += 1
        except Exception as exc:  # noqa: BLE001 — una cadena rota no frena a las demás
            fallidas += 1
            print(f"  [{slug}] cadena {fila['id']} NO invalidada: {exc!r}", file=sys.stderr)
    await asentar("fin", {"candidatas": len(ids), "invalidadas": hechas, "fallidas": fallidas})
    return (hechas, fallidas)


async def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Invalida las cadenas pendientes sin huella de contenido (AREA-04-09)"
    )
    parser.add_argument(
        "--operador", required=True,
        help="auth_users.username de quien responde por el acto (debe existir en cada tenant)",
    )
    parser.add_argument("--tenant", help="slug de un solo tenant (default: todos)")
    parser.add_argument("--dry-run", action="store_true", help="solo cuenta; no escribe nada")
    args = parser.parse_args(argv)
    if not _AUDIT_AVAILABLE:
        raise RuntimeError(
            "orpycamcp_common (auditoría) no disponible — se aborta: no se invalida "
            "una cadena de firma sin dejar asiento"
        )

    conn = await asyncpg.connect(dsn=_require_dsn())
    total = fallidas_total = 0
    try:
        objetivo = [
            (sch, slug_de(sch))
            for sch in await _tenant_schemas(conn)
            if not args.tenant or slug_de(sch) == args.tenant
        ]
        # Preflight: el operador debe existir en TODOS los tenants a procesar,
        # antes de escribir nada (un lote a medias con otro actor no se admite).
        operadores: dict[str, uuid.UUID] = {}
        faltan: list[str] = []
        for schema, slug in objetivo:
            await conn.execute(f'SET search_path = "{schema}"')
            if await conn.fetchval("SELECT to_regclass('firma_solicitud')") is None:
                continue  # sin signature-service migrado: nada que procesar
            oid = await resolver_operador(conn, schema=schema, username=args.operador)
            if oid is None:
                faltan.append(slug)
            else:
                operadores[schema] = oid
        if faltan:
            print(
                f"ABORTADO: el operador {args.operador!r} no existe o no está activo en "
                f"auth_users de: {', '.join(faltan)}. No se escribió nada.",
                file=sys.stderr,
            )
            return 2
        for schema, slug in objetivo:
            if schema not in operadores:
                continue
            hechas, fallidas = await invalidar_en_tenant(
                conn, schema=schema, slug=slug, dry_run=args.dry_run,
                operador_id=operadores[schema], operador=args.operador,
            )
            total += hechas
            fallidas_total += fallidas
        await conn.execute("SET search_path = public")
    finally:
        await conn.close()
    verbo = "se invalidarían" if args.dry_run else "invalidada(s)"
    print(f"Total: {total} {verbo}, {fallidas_total} fallida(s)")
    return 1 if fallidas_total else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
