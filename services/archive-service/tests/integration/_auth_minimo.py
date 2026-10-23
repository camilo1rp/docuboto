# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tablas RBAC mínimas de un schema de prueba (C11).

Las migraciones de archive-service no crean `auth_users` / `auth_memberships` /
`role_clearance` (son de auth-service), pero el `INSERT ... SELECT` del préstamo
las lee para validar y medir al prestatario. En el tier de integración se crean
aquí con la FORMA mínima real (mismos nombres de columna que
`auth-service/migrations/tenant/001_rbac.sql`) y se siembran por test."""

from __future__ import annotations

import uuid

import asyncpg

from app.core.database import tenant_schema

_DDL = """
CREATE TABLE IF NOT EXISTS {s}.auth_users (
    id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    username  VARCHAR(80) NOT NULL UNIQUE,
    is_root   BOOLEAN NOT NULL DEFAULT false,
    is_active BOOLEAN NOT NULL DEFAULT true
);
CREATE TABLE IF NOT EXISTS {s}.auth_memberships (
    user_id  UUID NOT NULL REFERENCES {s}.auth_users(id) ON DELETE CASCADE,
    group_id UUID NOT NULL
);
CREATE TABLE IF NOT EXISTS {s}.role_clearance (
    group_id  UUID PRIMARY KEY,
    max_level INT NOT NULL
);
"""


async def crear_tablas_auth(conn: asyncpg.Connection, slug: str) -> None:
    await conn.execute(_DDL.format(s=f'"{tenant_schema(slug)}"'))


async def crear_usuario(
    conn: asyncpg.Connection,
    slug: str,
    *,
    nivel: int | None = None,
    activo: bool = True,
    root: bool = False,
) -> uuid.UUID:
    """Usuario con clearance `nivel` (None = sin membresías => 1, como el resolutor)."""
    s = f'"{tenant_schema(slug)}"'
    uid: uuid.UUID = await conn.fetchval(
        f"INSERT INTO {s}.auth_users (username, is_root, is_active) "
        "VALUES ($1, $2, $3) RETURNING id",
        f"u-{uuid.uuid4().hex[:10]}",
        root,
        activo,
    )
    if nivel is not None:
        gid = uuid.uuid4()
        await conn.execute(f"INSERT INTO {s}.role_clearance VALUES ($1, $2)", gid, nivel)
        await conn.execute(f"INSERT INTO {s}.auth_memberships VALUES ($1, $2)", uid, gid)
    return uid
