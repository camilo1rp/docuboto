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

    async def get_user_by_username(self, username: str) -> asyncpg.Record | None:
        """Usado por el script de ops `grant_root` (E08) para resolver el usuario
        a promover a partir de su username (los usernames se guardan en mayúsculas,
        ver `create_user`)."""
        return await self._conn.fetchrow(
            "SELECT id, username, email, keycloak_sub, is_root, is_active "
            "FROM auth_users WHERE username = $1",
            username.upper(),
        )

    async def set_root(self, user_id: uuid.UUID) -> asyncpg.Record:
        """Promueve un usuario a ROOT. Solo invocado por el script de ops
        `grant_root` — la API HTTP nunca voltea `is_root` a true por sí misma
        (ver el docstring de `upsert_user_by_keycloak_sub` y el hallazgo Alto
        de auto-escalada que cierra `RBACService.create_user`)."""
        return await self._conn.fetchrow(
            "UPDATE auth_users SET is_root = true WHERE id = $1 "
            "RETURNING id, username, email, keycloak_sub, is_root, is_active",
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

    async def get_group(self, group_id: uuid.UUID) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            "SELECT id, name, description FROM auth_groups WHERE id = $1", group_id
        )

    # --- permisos -----------------------------------------------------------
    async def list_permissions(self) -> list[asyncpg.Record]:
        return await self._conn.fetch(
            "SELECT id, nombre, descripcion FROM auth_permissions ORDER BY nombre"
        )

    async def get_permission_name(self, permission_id: uuid.UUID) -> str | None:
        """``nombre`` de un permiso por id, o ``None`` si no existe.

        Usado por el gate de no-auto-concesión de ``USUA_PERM_ROOT``
        (`RBACService.set_group_permission`): el endpoint solo recibe el
        UUID, así que hace falta resolver el nombre para compararlo.
        """
        return await self._conn.fetchval(
            "SELECT nombre FROM auth_permissions WHERE id = $1", permission_id
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

    async def list_group_members(
        self, group_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[asyncpg.Record], int]:
        """Usuarios del grupo (espejo de lectura de `add_membership`/`remove_membership`)."""
        rows = await self._conn.fetch(
            """
            SELECT u.id, u.username, u.email, u.keycloak_sub, u.is_root, u.is_active
            FROM auth_memberships m
            JOIN auth_users u ON u.id = m.user_id
            WHERE m.group_id = $1
            ORDER BY u.username
            LIMIT $2 OFFSET $3
            """,
            group_id,
            limit,
            offset,
        )
        total: int = await self._conn.fetchval(
            "SELECT count(*) FROM auth_memberships WHERE group_id = $1", group_id
        )
        return rows, total

    async def list_group_permissions(self, group_id: uuid.UUID) -> list[asyncpg.Record]:
        """Permisos asignados al grupo (espejo de lectura de `set_group_permission`)."""
        return await self._conn.fetch(
            """
            SELECT ap.id AS permission_id, ap.nombre, ap.descripcion, agp.crud
            FROM auth_group_permissions agp
            JOIN auth_permissions ap ON ap.id = agp.permission_id
            WHERE agp.group_id = $1
            ORDER BY ap.nombre
            """,
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
