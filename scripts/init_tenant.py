# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
init_tenant.py — Inicializar un nuevo tenant en OrpycaMCP.

Aplica la cadena de migraciones (ADR-053) a `public` y al esquema del tenant,
registra el tenant en public.tenants y crea su cuenta de servicio de inteligencia
en `public.auth_service_accounts` (ADR-048 §3). Su token lleva la audiencia del
proveedor, así que no entra al API de Docuboto.

Lo aplicado se anota en `public.schema_migrations`, el mismo libro y con el mismo
cerrojo que `services/docuboto/migrations/runner.py` (ver
`orpycamcp_common.migrations`).

Uso:
    python scripts/init_tenant.py --slug icetex --name "ICETEX" --code "ICETEX"

Variables de entorno requeridas:
    DATABASE_URL                  postgresql+asyncpg://... o postgresql://...
    INTELLIGENCE_CREDENTIAL_KEK   sella la clave de seudónimo de la entidad
    IDENTITY_ISSUER               emisor que se imprime para declarar en el proveedor
"""

import argparse
import asyncio
import os
import re
import secrets
import sys
from pathlib import Path
from typing import Any

import asyncpg
from orpycamcp_common import migrations as ledger

# ---------------------------------------------------------------------------
# The one migration chain (ADR-053): `global/` to `public`, `tenant/` to the
# schema of the new tenant. Same ledger and same lock as the runner.
# ---------------------------------------------------------------------------

BASE = Path(__file__).parent.parent
MIGRATIONS_DIR = BASE / "services" / "docuboto" / "migrations"


def _dsn(raw: str) -> str:
    return raw.replace("postgresql+asyncpg://", "postgresql://")


# ---------------------------------------------------------------------------
# Schema del tenant — creado por tenant-service; aquí lo garantizamos
# ---------------------------------------------------------------------------

async def provision_schema(conn: asyncpg.Connection, slug: str) -> None:
    schema = f"tenant_{slug.replace('-', '_')}"
    print(f"  [schema] CREATE SCHEMA IF NOT EXISTS {schema}")
    await conn.execute(f"CREATE SCHEMA IF NOT EXISTS {schema}")
    # El rol de la aplicación necesita USAR el esquema nuevo: los privilegios por
    # omisión cubren sus tablas, no el esquema en sí (ver migración 008).
    if await conn.fetchval("SELECT 1 FROM pg_roles WHERE rolname = 'orpycamcp_app'"):
        await conn.execute(f"GRANT USAGE ON SCHEMA {schema} TO orpycamcp_app")
        print(f"  [schema] GRANT USAGE ON {schema} TO orpycamcp_app")


# ---------------------------------------------------------------------------
# Registro del tenant en public.tenants
# ---------------------------------------------------------------------------

async def register_tenant(
    conn: asyncpg.Connection, slug: str, name: str, code: str
) -> None:
    existing = await conn.fetchrow(
        "SELECT id, slug FROM public.tenants WHERE slug = $1", slug
    )
    if existing:
        print(f"  [tenant] ya existe — slug={slug}, id={existing['id']}")
        return
    row = await conn.fetchrow(
        """
        INSERT INTO public.tenants (slug, name, code)
        VALUES ($1, $2, $3)
        RETURNING id
        """,
        slug, name, code,
    )
    print(f"  [tenant] registrado — slug={slug}, id={row['id']}")


# ---------------------------------------------------------------------------
# Cliente de servicio de inteligencia (ADR-045 §4)
#
# La cuenta de servicio de inteligencia (ADR-048 §3). No tiene secreto: el
# token lo firma el propio proceso al hablar con el proveedor, y una fila sin
# `secret_hash` no sirve en `/oauth/token`. Este paso imprime el par
# (issuer, client_id) que se declara del lado del proveedor.
# ---------------------------------------------------------------------------

FINALIDAD_SEUDONIMO = "pseudonym"
SLOT_SEUDONIMO = 1


def esquema_de(slug: str) -> str:
    """`tenant_{slug}` con guiones pasados a guion bajo. El slug ya está acotado."""
    if not re.match(r"^[a-z0-9-]+$", slug):
        raise ValueError("slug fuera de forma")
    return "tenant_" + slug.replace("-", "_")


def id_cliente_inteligencia(slug: str) -> str:
    """Identificador estable de la cuenta de servicio de esa entidad."""
    esquema_de(slug)
    return f"intelligence-{slug}"


# Audiencia que el proveedor exige en el access token. Literales adyacentes:
# el árbol de código no escribe el nombre del proveedor.
AUDIENCIA_PROVEEDOR = "pro" "boto-api"


def aad_credencial(slug: str, finalidad: str, slot: int) -> bytes:
    """AAD atada a la entidad, la finalidad y la ranura. Una fila copiada no descifra."""
    return f"tenant:{slug}:intelligence:{finalidad}:{slot}".encode()


async def asegurar_clave_seudonimo(conn: Any, slug: str, *, key: bytes) -> None:
    """Sella una clave HMAC nueva si la ranura de seudónimo está vacía.

    Reejecutar no la rota. El id de quien consulta se sustituye por el HMAC
    de esta clave; sin ella la pasarela no pregunta al proveedor.
    """
    from orpycamcp_common.kek import seal

    fila = await conn.fetchrow(
        "SELECT purpose FROM intelligence_credentials WHERE purpose = $1 AND slot = $2",
        FINALIDAD_SEUDONIMO,
        SLOT_SEUDONIMO,
    )
    if fila is not None:
        return
    ciphertext, nonce = seal(
        secrets.token_bytes(32),
        key=key,
        aad=aad_credencial(slug, FINALIDAD_SEUDONIMO, SLOT_SEUDONIMO),
    )
    await conn.execute(
        """
        INSERT INTO intelligence_credentials
            (purpose, slot, ciphertext, nonce, kek_version)
        VALUES ($1, $2, $3, $4, 1)
        """,
        FINALIDAD_SEUDONIMO,
        SLOT_SEUDONIMO,
        ciphertext,
        nonce,
    )


async def aprovisionar_cuenta_inteligencia(
    conn: Any, slug: str, *, key: bytes, issuer: str
) -> str:
    """Crea la cuenta de servicio y la clave de seudónimo si faltan. Idempotente.

    Devuelve la línea que se imprime: issuer y client_id.
    """
    client_id = id_cliente_inteligencia(slug)
    insertada = await conn.fetchval(
        """
        INSERT INTO public.auth_service_accounts (client_id, tenant_slug, audience)
        VALUES ($1, $2, $3)
        ON CONFLICT (client_id) DO NOTHING
        RETURNING client_id
        """,
        client_id,
        slug,
        AUDIENCIA_PROVEEDOR,
    )
    await conn.execute(f'SET search_path = "{esquema_de(slug)}"')
    try:
        await asegurar_clave_seudonimo(conn, slug, key=key)
    finally:
        await conn.execute("SET search_path = public")
    estado = "cuenta de servicio" if insertada else "ya existía"
    return f"[intelligence] {estado} — issuer={issuer} client_id={client_id}"


def _exigir_entorno(nombre: str) -> str:
    valor = os.environ.get(nombre, "").strip()
    if not valor:
        print(f"ERROR: {nombre} no está definido", file=sys.stderr)
        sys.exit(1)
    return valor


def _kek_credencial() -> bytes:
    from orpycamcp_common.kek import decode_aes256

    cruda = _exigir_entorno("INTELLIGENCE_CREDENTIAL_KEK")
    try:
        return decode_aes256(cruda, "INTELLIGENCE_CREDENTIAL_KEK")
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

async def main(slug: str, name: str, code: str) -> None:
    raw_dsn = os.environ.get("MIGRATIONS_DATABASE_URL", "") or os.environ.get("DATABASE_URL", "")
    if not raw_dsn:
        print("ERROR: DATABASE_URL no está definido", file=sys.stderr)
        sys.exit(1)

    dsn = _dsn(raw_dsn)
    print("\nConectando a PostgreSQL...")
    conn = await asyncpg.connect(dsn=dsn)

    try:
        # Mismo cerrojo global que el runner (C19, ADR-041): provisionar un tenant
        # mientras arranca `migrate-docuboto` no los deja pisarse.
        async with ledger.cerrojo(conn):
            await ledger.ensure_ledger(conn)

            print("\n── Migraciones globales (public schema) ──────────────────────")
            await ledger.apply_chain(conn, MIGRATIONS_DIR / "global", ledger.ESQUEMA_GLOBAL)

            print(f"\n── Schema del tenant: {esquema_de(slug)} ─────")
            await provision_schema(conn, slug)

            print("\n── Migraciones por tenant ────────────────────────────────────")
            await ledger.apply_chain(conn, MIGRATIONS_DIR / "tenant", esquema_de(slug))

            print("\n── Registro en public.tenants ────────────────────────────────")
            await register_tenant(conn, slug, name, code)

            print("\n── Cuenta de servicio de inteligencia ────────────────────────")
            linea = await aprovisionar_cuenta_inteligencia(
                conn,
                slug,
                key=_kek_credencial(),
                issuer=_exigir_entorno("IDENTITY_ISSUER"),
            )
            print(f"  {linea}")

            print(f"\n✓ Tenant '{slug}' inicializado correctamente.\n")
    finally:
        await conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Inicializar tenant en OrpycaMCP")
    parser.add_argument("--slug", required=True, help="Slug único del tenant (ej: icetex)")
    parser.add_argument("--name", required=True, help="Nombre completo (ej: 'ICETEX')")
    parser.add_argument("--code", required=True, help="Código para radicados (ej: 'ICETEX')")
    args = parser.parse_args()

    # Validaciones básicas
    if not re.match(r"^[a-z0-9-]+$", args.slug):
        print("ERROR: slug solo puede contener minúsculas, números y guiones", file=sys.stderr)
        sys.exit(1)
    if not re.match(r"^[A-Z0-9_]+$", args.code):
        print("ERROR: code solo puede contener mayúsculas, números y guiones bajos", file=sys.stderr)
        sys.exit(1)

    asyncio.run(main(args.slug, args.name, args.code))
