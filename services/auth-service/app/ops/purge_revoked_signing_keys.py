# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Purga del material privado de las credenciales de firma REVOCADAS (E17/F4).

Minimización de datos: una privada de FIRMA revocada no tiene uso operativo (el
path de firma exige `estado='activo'`; verificación y `revocation-status` usan
solo el cert PÚBLICO + estado en BD, nunca la privada). Tras la ventana de
retención `signing_key_purge_after_days` se anula `private_key_cifrado`/
`private_key_nonce`, conservando TODO lo público (la fila NO se borra).

Canal de OPS, NO endpoint HTTP (familia de `rewrap_signing_keys`):

    python -m app.ops.purge_revoked_signing_keys --dry-run          # cuenta lo purgable
    python -m app.ops.purge_revoked_signing_keys                    # purga (todos los tenants)
    python -m app.ops.purge_revoked_signing_keys --tenant icetex    # solo un tenant

INVARIANTES (fail-closed):
 - Solo credenciales `estado='revocado'` con la ventana vencida
   (`COALESCE(revocado_at, updated_at) < now() - dias`). El CHECK del motor
   (`chk_auth_user_signing_key_purga`) rechaza además cualquier NULL de material
   sobre una fila no-revocada.
 - IRREVERSIBLE por diseño (re-enroll genera un par nuevo). La ventana es un
   colchón operacional, no reversibilidad criptográfica.
 - tx por-fila con `FOR UPDATE` + asiento `signing_key.private_purged` (metadatos:
   user_id, revocado_at; NUNCA material) en la MISMA tx. Idempotente
   (`private_key_cifrado IS NOT NULL`). Exit-code != 0 si algo falla.
 - Aborta si la auditoría no está disponible (no destruir material sin traza).
 - Desbloquea retiradas de KEK: una purgada ya no cuenta en `count_by_key_version`.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

import asyncpg

from app.core.config import settings
from app.repositories.signing_key_repository import SigningKeyRepository

try:
    from orpycamcp_common import audit as _audit

    _AUDIT_AVAILABLE = True
except Exception:  # noqa: BLE001
    _audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

_SERVICE = "auth-service"
_DSN = os.environ["DATABASE_URL"].replace("postgresql+asyncpg://", "postgresql://")


async def _tenant_schemas(conn: asyncpg.Connection) -> list[str]:
    rows = await conn.fetch(
        "SELECT schema_name FROM information_schema.schemata "
        "WHERE schema_name LIKE 'tenant\\_%' ESCAPE '\\' ORDER BY schema_name"
    )
    return [r["schema_name"] for r in rows]


async def _purge_tenant(
    conn: asyncpg.Connection, *, schema: str, slug: str, days: int, dry_run: bool
) -> tuple[int, int]:
    """Purga las credenciales revocadas vencidas de un tenant. (hechas, fallidas)."""
    await conn.execute(f'SET search_path = "{schema}", public')
    repo = SigningKeyRepository(conn)
    pendientes = await repo.fetch_ids_to_purge(days)
    if not pendientes:
        return (0, 0)
    if dry_run:
        print(f"  [{slug}] {len(pendientes)} credencial(es) revocada(s) se purgarían")
        return (len(pendientes), 0)

    hechas = 0
    fallidas = 0
    for p in pendientes:
        try:
            async with conn.transaction():
                await conn.execute(f'SET LOCAL search_path = "{schema}", public')
                repo_tx = SigningKeyRepository(conn)
                row = await repo_tx.get_for_rewrap(p["id"])  # FOR UPDATE, cualquier estado
                if row is None or row["private_key_cifrado"] is None:
                    continue  # ya purgada / carrera -> idempotente
                if row["estado"] != "revocado":
                    continue  # invariante: solo revocadas (el CHECK lo reforzaría)
                await repo_tx.purge(row["id"])
                if _AUDIT_AVAILABLE:
                    await _audit.append(
                        conn,
                        tenant_slug=slug,
                        service=_SERVICE,
                        action="signing_key.private_purged",
                        object_type="signing_key",
                        object_ref=str(row["user_id"]),
                        actor="system:key-purge",
                        payload={
                            # Metadatos PÚBLICOS que hacen el asiento
                            # auto-suficiente para identificar la credencial sin
                            # cross-ref — NUNCA material privado.
                            "revocado_at": row["revocado_at"].isoformat()
                            if row["revocado_at"]
                            else None,
                            "serial": row["serial"],
                            "certificado_fingerprint": row["certificado_fingerprint"],
                        },
                    )
            hechas += 1
        except Exception as exc:  # noqa: BLE001 — aísla el fallo por-fila
            fallidas += 1
            print(f"  [{slug}] FALLO al purgar {p['id']}: {exc}", file=sys.stderr)
    print(f"  [{slug}] purgadas: {hechas}; fallidas: {fallidas}")
    return (hechas, fallidas)


async def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Purga del material privado de credenciales de firma revocadas (E17/F4)"
    )
    parser.add_argument("--tenant", help="slug de un solo tenant (default: todos)")
    parser.add_argument("--dry-run", action="store_true", help="solo cuenta lo purgable")
    args = parser.parse_args(argv)

    days = settings.signing_key_purge_after_days
    if not args.dry_run and not _AUDIT_AVAILABLE:
        raise RuntimeError(
            "orpycamcp_common (auditoría) no disponible — se aborta la purga "
            "para no destruir material privado sin traza"
        )

    conn = await asyncpg.connect(dsn=_DSN)
    try:
        print(f"Purga de material privado revocado (ventana {days}d){' (dry-run)' if args.dry_run else ''}")
        total_hechas = 0
        total_fallidas = 0
        for schema in await _tenant_schemas(conn):
            # Inverso EXACTO de tenant_schema (`_`->`-`, biyectivo: el validador
            # de slug prohíbe `_`) — solo relevante para el AAD, pero se mantiene
            # consistente con rewrap_signing_keys.
            slug = schema[len("tenant_"):].replace("_", "-") if schema.startswith("tenant_") else schema
            if args.tenant and slug != args.tenant:
                continue
            hechas, fallidas = await _purge_tenant(
                conn, schema=schema, slug=slug, days=days, dry_run=args.dry_run
            )
            total_hechas += hechas
            total_fallidas += fallidas
        await conn.execute("SET search_path = public")
        print(f"Total: {total_hechas} purgada(s), {total_fallidas} fallida(s)")
        return 1 if total_fallidas else 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
