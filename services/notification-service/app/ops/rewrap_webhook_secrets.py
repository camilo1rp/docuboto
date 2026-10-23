# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Rotación de la KEK de los secretos HMAC de webhooks (re-wrap).

Re-envuelve cada secreto cifrado bajo la KEK **activa**
(`WEBHOOK_SECRET_KEK_ACTIVE_VERSION`): lo descifra con la KEK de SU versión y lo
re-cifra con la activa. El AAD (`tenant:{slug}:webhook:{id}:secret`) no cambia;
solo cambia la KEK. Mismo procedimiento que auth-service con la firma personal.

Procedimiento: añadir la KEK nueva a `WEBHOOK_SECRET_KEKS` (`{"2": "<base64>"}`),
fijar `WEBHOOK_SECRET_KEK_ACTIVE_VERSION=2`, reiniciar el servicio (conviven v1 y
v2 mientras dura la transición), y correr:

    python -m app.ops.rewrap_webhook_secrets --verify    # conteo por versión
    python -m app.ops.rewrap_webhook_secrets --dry-run
    python -m app.ops.rewrap_webhook_secrets             # todos los tenants
    python -m app.ops.rewrap_webhook_secrets --tenant icetex

INVARIANTES (fail-closed): una KEK vieja NO se retira mientras `--verify` muestre
filas en esa versión (retirarla es acto MANUAL del operador); round-trip antes de
persistir; nonce NUEVO por re-cifrado (jamás se reutiliza el viejo con otra KEK);
el texto claro es transitorio y nunca se loguea ni audita (el asiento
`notification.webhook.secret_rewrapped` lleva solo versiones); idempotente y
reanudable (solo toca `secret_kek_version <> activa`); exit-code != 0 si algo falla.
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import sys

import asyncpg

from app.core import webhook_secrets
from app.ops.cifrar_webhook_secrets import (
    _AUDIT_AVAILABLE,
    _SERVICE,
    _audit,
    _require_dsn,
    migracion_009_aplicada,
    tenant_schemas,
    verificar,
)
from app.repositories.webhook_repository import WebhookRepository

_ACTOR = "system:kek-rotation"


async def rewrap_tenant(
    conn: asyncpg.Connection, *, schema: str, dry_run: bool = False
) -> tuple[int, int]:
    """Re-envuelve los secretos de un tenant. Devuelve (hechas, fallidas)."""
    slug = webhook_secrets.slug_from_schema(schema)
    active_v, _ = webhook_secrets.active_key()
    ring = webhook_secrets.keyring()
    await conn.execute(f'SET search_path = "{schema}"')
    if not await migracion_009_aplicada(conn, schema):
        return (0, 0)
    pendientes = await WebhookRepository(conn).fetch_ids_to_rewrap(active_v)
    if not pendientes:
        return (0, 0)
    if dry_run:
        print(f"  [{slug}] {len(pendientes)} secreto(s) se re-envolverían")
        return (len(pendientes), 0)

    hechas = fallidas = 0
    for p in pendientes:
        try:
            async with conn.transaction():
                await conn.execute(f'SET LOCAL search_path = "{schema}"')
                repo = WebhookRepository(conn)
                row = await repo.get_for_rewrap(p["id"])
                if row is None or row["secret_kek_version"] == active_v:
                    continue  # carrera con otra corrida/borrado: idempotente
                if row["secret_kek_version"] not in ring:
                    raise RuntimeError(f"KEK v{row['secret_kek_version']} ausente — no re-envolvible")
                plano = webhook_secrets.decrypt_secret(
                    bytes(row["secret_cifrado"]),
                    nonce=bytes(row["secret_nonce"]),
                    version=row["secret_kek_version"],
                    tenant_slug=slug,
                    webhook_id=row["id"],
                )
                ct, nonce, version = webhook_secrets.encrypt_secret(
                    plano, tenant_slug=slug, webhook_id=row["id"]
                )
                vuelta = webhook_secrets.decrypt_secret(
                    ct, nonce=nonce, version=version, tenant_slug=slug, webhook_id=row["id"]
                )
                if not hmac.compare_digest(vuelta.encode(), plano.encode()):
                    raise RuntimeError(f"round-trip falló para {row['id']} — no se persiste")
                del plano, vuelta
                await repo.rewrap(
                    row["id"], secret_cifrado=ct, secret_nonce=nonce, secret_kek_version=version
                )
                await _audit.append(
                    conn,
                    tenant_slug=slug,
                    service=_SERVICE,
                    action="notification.webhook.secret_rewrapped",
                    object_type="webhook",
                    object_ref=str(row["id"]),
                    actor=_ACTOR,
                    payload={"from_version": row["secret_kek_version"], "to_version": active_v},
                    canal="system",
                )
            hechas += 1
        except Exception as exc:  # noqa: BLE001 — aísla el fallo por fila
            fallidas += 1
            print(f"  [{slug}] FALLO al re-envolver {p['id']}: {type(exc).__name__}", file=sys.stderr)
    print(f"  [{slug}] re-envueltas: {hechas}; fallidas: {fallidas}")
    return (hechas, fallidas)


async def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rotación de la KEK de secretos de webhooks")
    parser.add_argument("--tenant", help="slug de un solo tenant (default: todos)")
    parser.add_argument("--dry-run", action="store_true", help="solo cuenta")
    parser.add_argument("--verify", action="store_true", help="conteo por versión de KEK")
    args = parser.parse_args(argv)

    conn = await asyncpg.connect(dsn=_require_dsn())
    try:
        active_v, _ = webhook_secrets.active_key()  # fail-closed
        if args.verify:
            print(f"KEK activa: v{active_v}; versiones cargadas: {sorted(webhook_secrets.keyring())}")
            return await verificar(conn)
        if not args.dry_run and not _AUDIT_AVAILABLE:
            raise RuntimeError(
                "orpycamcp_common (auditoría) no disponible — se aborta el re-wrap sin traza"
            )
        print(f"Re-wrap a KEK activa v{active_v}{' (dry-run)' if args.dry_run else ''}")
        total_h = total_f = 0
        for schema in await tenant_schemas(conn):
            if args.tenant and webhook_secrets.slug_from_schema(schema) != args.tenant:
                continue
            h, f = await rewrap_tenant(conn, schema=schema, dry_run=args.dry_run)
            total_h += h
            total_f += f
        await conn.execute("SET search_path = public")
        print(f"Total: {total_h} re-envuelta(s), {total_f} fallida(s)")
        return 1 if total_f else 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
