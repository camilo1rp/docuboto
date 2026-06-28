# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Acceso a datos del RBAC (E08). SQL crudo; las tablas viven en el schema del
tenant (search_path fijado por get_tenant_conn)."""

import uuid

import asyncpg


class RBACRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    # --- usuarios -----------------------------------------------------------
    async def create_user(
        self,
        *,
        username: str,
        email: str | None,
        keycloak_sub: str | None,
        is_root: bool,
    ) -> asyncpg.Record:
        return await self._conn.fetchrow(
            """
            INSERT INTO auth_users (username, email, keycloak_sub, is_root)
            VALUES ($1, $2, $3, $4)
            RETURNING id, username, email, keycloak_sub, is_root, is_active
            """,
            username.upper(),
            email,
            keycloak_sub,
            is_root,
        )

    async def get_user(self, user_id: uuid.UUID) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            "SELECT id, username, email, keycloak_sub, is_root, is_active "
            "FROM auth_users WHERE id = $1",
            user_id,
        )

    async def get_user_by_keycloak_sub(self, sub: str) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            "SELECT id, username, email, keycloak_sub, is_root, is_active "
            "FROM auth_users WHERE keycloak_sub = $1",
            sub,
        )

    async def upsert_user_by_keycloak_sub(
        self,
        *,
        keycloak_sub: str,
        username: str,
        email: str,
    ) -> asyncpg.Record:
        """Lazy-provision a user row from Keycloak claims.

        Uses ON CONFLICT (keycloak_sub) to handle concurrent first-login races
        without a separate SELECT step. Never flips is_root — admin must set it
        manually. Returns the full row including the internal id.
        """
        return await self._conn.fetchrow(
            """
            INSERT INTO auth_users (username, email, keycloak_sub, is_root)
            VALUES ($1, $2, $3, false)
            ON CONFLICT (keycloak_sub) DO UPDATE
                SET username = EXCLUDED.username,
                    email    = EXCLUDED.email
            RETURNING id, username, email, keycloak_sub, is_root, is_active
            """,
            (username.upper() if username else "UNKNOWN"),
            (email or None),
            keycloak_sub,
        )

    async def list_users(self, *, limit: int, offset: int) -> tuple[list[asyncpg.Record], int]:
        rows = await self._conn.fetch(
            "SELECT id, username, email, keycloak_sub, is_root, is_active "
            "FROM auth_users ORDER BY username LIMIT $1 OFFSET $2",
            limit,
            offset,
        )
        total: int = await self._conn.fetchval("SELECT count(*) FROM auth_users")
        return rows, total

    async def list_pickable_users(self, *, limit: int) -> list[asyncpg.Record]:
        """Listado mínimo (id + username) de usuarios activos, para selectores de UI.

        D-10: alimenta el `UserPicker` (cadena de visto bueno) sin exponer `email`
        ni metadatos administrativos. Gateado con `PERM_RADI` en el router (no admin).
        """
        return await self._conn.fetch(
            "SELECT id, username FROM auth_users "
            "WHERE is_active = true ORDER BY username LIMIT $1",
            limit,
        )

    async def is_root(self, user_id: uuid.UUID) -> bool:
        val = await self._conn.fetchval("SELECT is_root FROM auth_users WHERE id = $1", user_id)
        return bool(val)

    # --- grupos -------------------------------------------------------------
    async def create_group(self, *, name: str, description: str | None) -> asyncpg.Record:
        return await self._conn.fetchrow(
            "INSERT INTO auth_groups (name, description) VALUES ($1, $2) "
            "RETURNING id, name, description",
            name,
            description,
        )

    async def list_groups(self) -> list[asyncpg.Record]:
        return await self._conn.fetch("SELECT id, name, description FROM auth_groups ORDER BY name")

    # --- permisos -----------------------------------------------------------
    async def list_permissions(self) -> list[asyncpg.Record]:
        return await self._conn.fetch(
            "SELECT id, nombre, descripcion FROM auth_permissions ORDER BY nombre"
        )

    # --- membresías y permisos de grupo ------------------------------------
    async def add_membership(self, *, user_id: uuid.UUID, group_id: uuid.UUID) -> None:
        await self._conn.execute(
            "INSERT INTO auth_memberships (user_id, group_id) VALUES ($1, $2) "
            "ON CONFLICT (user_id, group_id) DO NOTHING",
            user_id,
            group_id,
        )

    async def remove_membership(self, *, user_id: uuid.UUID, group_id: uuid.UUID) -> None:
        await self._conn.execute(
            "DELETE FROM auth_memberships WHERE user_id = $1 AND group_id = $2",
            user_id,
            group_id,
        )

    async def set_group_permission(
        self, *, group_id: uuid.UUID, permission_id: uuid.UUID, crud: int
    ) -> None:
        await self._conn.execute(
            """
            INSERT INTO auth_group_permissions (group_id, permission_id, crud)
            VALUES ($1, $2, $3)
            ON CONFLICT (group_id, permission_id) DO UPDATE SET crud = EXCLUDED.crud
            """,
            group_id,
            permission_id,
            crud,
        )

    # --- resolución de permisos (RN-2: MAX(crud) por permiso) ---------------
    async def resolve_permissions(self, user_id: uuid.UUID) -> dict[str, int]:
        rows = await self._conn.fetch(
            """
            SELECT ap.nombre, MAX(agp.crud) AS crud
            FROM auth_memberships am
            JOIN auth_group_permissions agp ON agp.group_id = am.group_id
            JOIN auth_permissions ap        ON ap.id = agp.permission_id
            WHERE am.user_id = $1 AND agp.crud > 0
            GROUP BY ap.nombre
            """,
            user_id,
        )
        return {r["nombre"]: r["crud"] for r in rows}
