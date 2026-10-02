# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Cifra en reposo los secretos HMAC de webhooks que quedaron EN CLARO.

La migración 009 añade las columnas cifradas pero NO puede cifrar: la KEK vive en
el entorno de la aplicación, no en la base. Este paso —idempotente, a ejecutar al
desplegar, DESPUÉS de las migraciones y ANTES de `up -d`— cifra cada `secret` en
claro con la KEK activa, deja `secret` en NULL en el MISMO UPDATE y escribe el
asiento `notification.webhook.secret_cifrado` en audit_log desde la aplicación
(misma transacción, nunca SQL directo; el asiento lleva solo metadatos).

    python -m app.ops.cifrar_webhook_secrets --verify     # cuántos quedan en claro
    python -m app.ops.cifrar_webhook_secrets --dry-run
    python -m app.ops.cifrar_webhook_secrets              # todos los tenants
    python -m app.ops.cifrar_webhook_secrets --tenant icetex

FAIL-CLOSED: sin KEK utilizable el script sale con error (y el servicio ni
arranca, ver `main.lifespan`); sin el módulo de auditoría no cifra nada (no se
muta un secreto sin traza). Además, mientras quede una fila en claro, el servicio
NO la entrega (`webhook_service._firma_de_la_entrega`): ni se firma con un texto
que sigue expuesto en la base ni se calla el hecho.

Round-trip obligatorio antes de persistir: nunca se escribe un ciphertext que no
descifre a EXACTAMENTE el original (sería un secreto perdido). Un fallo en una
fila no detiene las demás; exit-code != 0 si alguna falla. Re-ejecutar recoge lo
que quede (idempotente: solo toca filas con `secret IS NOT NULL`).
"""

from __future__ import annotations

import argparse
import asyncio
import hmac
import os
import sys

import asyncpg

from app.core import webhook_secrets
from app.repositories.webhook_repository import WebhookRepository

try:
    from orpycamcp_common import audit as _audit

    _AUDIT_AVAILABLE = True
except Exception:  # noqa: BLE001
    _audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

_SERVICE = "notification-service"
_ACTOR = "system:webhook-secret-encryption"

# Perezoso: importar este módulo (los tests lo hacen) no debe exigir DATABASE_URL.
_RAW_DSN = os.environ.get("MIGRATIONS_DATABASE_URL", "") or os.environ.get("DATABASE_URL", "")
_DSN = _RAW_DSN.replace("postgresql+asyncpg://", "postgresql://") if _RAW_DSN else ""


def _require_dsn() -> str:
    if not _DSN:
        raise RuntimeError("DATABASE_URL no está definida: este canal de ops necesita PostgreSQL.")
    return _DSN


async def tenant_schemas(conn: asyncpg.Connection) -> list[str]:
    rows = await conn.fetch(
        "SELECT schema_name FROM information_schema.schemata "
        "WHERE schema_name LIKE 'tenant\\_%' ESCAPE '\\' ORDER BY schema_name"
    )
    return [r["schema_name"] for r in rows]


async def migracion_009_aplicada(conn: asyncpg.Connection, schema: str) -> bool:
    """¿Existen la tabla y las columnas cifradas en este schema? (si no, se omite)."""
    return bool(
        await conn.fetchval(
            "SELECT count(*) = 3 FROM information_schema.columns "
            "WHERE table_schema = $1 AND table_name = 'webhook_subscription' "
            "AND column_name IN ('secret_cifrado', 'secret_nonce', 'secret_kek_version')",
            schema,
        )
    )


async def _validar_check(conn: asyncpg.Connection, slug: str) -> None:
    """Pasa `chk_webhook_secret_sin_claro` de NOT VALID a validado (idempotente).

    Requiere ser dueño de la tabla; con otro rol se avisa y se sigue."""
    try:
        await conn.execute(
            "ALTER TABLE webhook_subscription VALIDATE CONSTRAINT chk_webhook_secret_sin_claro"
        )
    except asyncpg.PostgresError as exc:
        print(f"  [{slug}] aviso: no se validó el CHECK ({type(exc).__name__})", file=sys.stderr)


async def cifrar_tenant(
    conn: asyncpg.Connection, *, schema: str, dry_run: bool = False
) -> tuple[int, int]:
    """Cifra los secretos en claro de UN tenant. Devuelve (hechas, fallidas)."""
    slug = webhook_secrets.slug_from_schema(schema)
    await conn.execute(f'SET search_path = "{schema}", public')
    if not await migracion_009_aplicada(conn, schema):
        print(f"  [{slug}] migración 009 sin aplicar: se omite")
        return (0, 0)
    pendientes = await WebhookRepository(conn).count_plaintext()
    if pendientes == 0:
        # Nada en claro (tenant sin webhooks, o ya cifrados): el CHECK que impide
        # escribir claro nuevo puede pasar a validado.
        if not dry_run:
            await _validar_check(conn, slug)
        return (0, 0)
    if dry_run:
        print(f"  [{slug}] {pendientes} secreto(s) en claro se cifrarían")
        return (pendientes, 0)

    hechas = 0
    fallidas = 0
    active_v, _ = webhook_secrets.active_key()
    # Una transacción por FILA: un fallo no revierte las demás ni deja a medias.
    for wid in await WebhookRepository(conn).fetch_plaintext_ids():
        try:
            async with conn.transaction():
                await conn.execute(f'SET LOCAL search_path = "{schema}", public')
                repo = WebhookRepository(conn)
                fila = await repo.get_plaintext_for_update(wid)
                if fila is None:
                    continue  # otra corrida ya la cifró: idempotente
                plano = fila["secret"]
                ct, nonce, version = webhook_secrets.encrypt_secret(
                    plano, tenant_slug=slug, webhook_id=wid
                )
                vuelta = webhook_secrets.decrypt_secret(
                    ct, nonce=nonce, version=version, tenant_slug=slug, webhook_id=wid
                )
                if not hmac.compare_digest(vuelta.encode(), plano.encode()):
                    raise RuntimeError(f"round-trip falló para {wid} — no se persiste")
                del plano, vuelta
                await repo.seal_legacy(
                    wid, secret_cifrado=ct, secret_nonce=nonce, secret_kek_version=version
                )
                await _audit.append(
                    conn,
                    tenant_slug=slug,
                    service=_SERVICE,
                    action="notification.webhook.secret_cifrado",
                    object_type="webhook",
                    object_ref=str(wid),
                    actor=_ACTOR,
                    payload={"kek_version": active_v, "origen": "secreto_en_claro_heredado"},
                )
            hechas += 1
        except Exception as exc:  # noqa: BLE001 — aísla el fallo por fila
            fallidas += 1
            print(f"  [{slug}] FALLO al cifrar {wid}: {type(exc).__name__}", file=sys.stderr)
    if fallidas == 0:
        await _validar_check(conn, slug)
    print(f"  [{slug}] cifradas: {hechas}; fallidas: {fallidas}")
    return (hechas, fallidas)


async def verificar(conn: asyncpg.Connection) -> int:
    """Informa cuántas filas quedan en claro y cuántas por versión de KEK.
    Exit-code 1 si queda alguna en claro (útil como compuerta de despliegue)."""
    en_claro = 0
    por_version: dict[int, int] = {}
    for schema in await tenant_schemas(conn):
        await conn.execute(f'SET search_path = "{schema}", public')
        if not await migracion_009_aplicada(conn, schema):
            if await conn.fetchval("SELECT to_regclass('webhook_subscription') IS NOT NULL"):
                print(f"  [{schema}] tiene webhooks pero la migración 009 no está aplicada")
                en_claro += 1
            continue
        repo = WebhookRepository(conn)
        en_claro += await repo.count_plaintext()
        for r in await repo.count_by_kek_version():
            por_version[r["key_version"]] = por_version.get(r["key_version"], 0) + r["n"]
    await conn.execute("SET search_path = public")
    print(f"Secretos en claro: {en_claro}")
    for v in sorted(por_version):
        print(f"  KEK v{v}: {por_version[v]} secreto(s) cifrado(s)")
    return 1 if en_claro else 0


async def run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cifra los secretos HMAC de webhooks en claro")
    parser.add_argument("--tenant", help="slug de un solo tenant (default: todos)")
    parser.add_argument("--dry-run", action="store_true", help="solo cuenta")
    parser.add_argument("--verify", action="store_true", help="cuántos quedan en claro")
    args = parser.parse_args(argv)

    conn = await asyncpg.connect(dsn=_require_dsn())
    try:
        if args.verify:
            return await verificar(conn)
        webhook_secrets.active_key()  # fail-closed: sin KEK no se hace nada
        if not args.dry_run and not _AUDIT_AVAILABLE:
            raise RuntimeError(
                "orpycamcp_common (auditoría) no disponible — se aborta el cifrado "
                "para no mutar secretos sin traza"
            )
        total_h = total_f = 0
        for schema in await tenant_schemas(conn):
            if args.tenant and webhook_secrets.slug_from_schema(schema) != args.tenant:
                continue
            h, f = await cifrar_tenant(conn, schema=schema, dry_run=args.dry_run)
            total_h += h
            total_f += f
        await conn.execute("SET search_path = public")
        print(f"Total: {total_h} cifrada(s), {total_f} fallida(s)")
        return 1 if total_f else 0
    finally:
        await conn.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(run()))
