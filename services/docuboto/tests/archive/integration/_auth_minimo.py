# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Usuarios con clearance en un schema de prueba (C11).

El `INSERT ... SELECT` del préstamo lee `auth_users` / `auth_memberships` /
`role_clearance` para validar y medir al prestatario. Las tablas las crea la
cadena de migraciones; aquí solo se siembran por test."""

from __future__ import annotations

import uuid

import asyncpg

from archive.core.database import tenant_schema


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
        gid: uuid.UUID = await conn.fetchval(
            f"INSERT INTO {s}.auth_groups (name) VALUES ($1) RETURNING id", f"g-{uuid.uuid4().hex[:10]}"
        )
        await conn.execute(
            f"INSERT INTO {s}.role_clearance (group_id, max_level) VALUES ($1, $2)", gid, nivel
        )
        await conn.execute(
            f"INSERT INTO {s}.auth_memberships (user_id, group_id) VALUES ($1, $2)", uid, gid
        )
    return uid
