# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""SQL del emisor de identidad (ADR-048).

Lo de la persona (`auth_users`, historial, refresh) vive en el schema de la
entidad: la conexión llega con su `search_path`. Las cuentas de servicio viven
en `public` y se nombran con el esquema.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import asyncpg

_PERSONA = (
    "SELECT id, username, email, password_hash, is_active, failed_logins, locked_until "
    "FROM auth_users"
)


class IdentityRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    # --- persona ------------------------------------------------------------
    async def user_by_username(self, username: str) -> asyncpg.Record | None:
        return await self._conn.fetchrow(f"{_PERSONA} WHERE username = $1", username.upper())

    async def user_by_id(self, user_id: uuid.UUID) -> asyncpg.Record | None:
        return await self._conn.fetchrow(f"{_PERSONA} WHERE id = $1", user_id)

    async def register_failure(
        self, user_id: uuid.UUID, *, max_failed: int, lockout_s: int
    ) -> datetime | None:
        """Suma un fallo; al llegar a `max_failed` bloquea y reinicia la cuenta."""
        return await self._conn.fetchval(
            """
            UPDATE auth_users SET
                locked_until = CASE WHEN failed_logins + 1 >= $2
                                    THEN now() + make_interval(secs => $3)
                                    ELSE locked_until END,
                failed_logins = CASE WHEN failed_logins + 1 >= $2 THEN 0
                                     ELSE failed_logins + 1 END
             WHERE id = $1
            RETURNING locked_until
            """,
            user_id,
            max_failed,
            float(lockout_s),
        )

    async def register_success(self, user_id: uuid.UUID) -> None:
        await self._conn.execute(
            "UPDATE auth_users SET failed_logins = 0, locked_until = NULL WHERE id = $1",
            user_id,
        )

    async def recent_password_hashes(self, user_id: uuid.UUID, n: int) -> list[str]:
        filas = await self._conn.fetch(
            "SELECT password_hash FROM auth_password_history WHERE user_id = $1 "
            "ORDER BY created_at DESC LIMIT $2",
            user_id,
            n,
        )
        return [f["password_hash"] for f in filas]

    async def set_password(self, user_id: uuid.UUID, password_hash: str) -> None:
        """Fija la contraseña, la apunta en el historial y quita el bloqueo."""
        await self._conn.execute(
            "UPDATE auth_users SET password_hash = $2, password_changed_at = now(), "
            "failed_logins = 0, locked_until = NULL WHERE id = $1",
            user_id,
            password_hash,
        )
        await self._conn.execute(
            "INSERT INTO auth_password_history (user_id, password_hash) VALUES ($1, $2)",
            user_id,
            password_hash,
        )

    # --- refresh ------------------------------------------------------------
    async def insert_refresh(
        self,
        *,
        user_id: uuid.UUID,
        family_id: uuid.UUID,
        token_hash: bytes,
        expires_at: datetime,
    ) -> None:
        await self._conn.execute(
            "INSERT INTO auth_refresh_tokens (user_id, family_id, token_hash, expires_at) "
            "VALUES ($1, $2, $3, $4)",
            user_id,
            family_id,
            token_hash,
            expires_at,
        )

    async def lock_refresh(self, token_hash: bytes) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            "SELECT id, user_id, family_id, expires_at, used_at, revoked_at, "
            "expires_at <= now() AS vencido "
            "FROM auth_refresh_tokens WHERE token_hash = $1 FOR UPDATE",
            token_hash,
        )

    async def mark_refresh_used(self, refresh_id: uuid.UUID) -> None:
        await self._conn.execute(
            "UPDATE auth_refresh_tokens SET used_at = now() WHERE id = $1", refresh_id
        )

    async def revoke_family(self, family_id: uuid.UUID) -> None:
        await self._conn.execute(
            "UPDATE auth_refresh_tokens SET revoked_at = now() "
            "WHERE family_id = $1 AND revoked_at IS NULL",
            family_id,
        )

    async def revoke_family_of(self, token_hash: bytes) -> None:
        await self._conn.execute(
            "UPDATE auth_refresh_tokens SET revoked_at = now() WHERE revoked_at IS NULL "
            "AND family_id = (SELECT family_id FROM auth_refresh_tokens WHERE token_hash = $1)",
            token_hash,
        )

    async def revoke_user_sessions(self, user_id: uuid.UUID) -> None:
        await self._conn.execute(
            "UPDATE auth_refresh_tokens SET revoked_at = now() "
            "WHERE user_id = $1 AND revoked_at IS NULL",
            user_id,
        )

    # --- cuentas de servicio (public) ---------------------------------------
    async def service_account(self, client_id: str) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            "SELECT client_id, secret_hash, tenant_slug, audience, scopes, is_active "
            "FROM public.auth_service_accounts WHERE client_id = $1",
            client_id,
        )

    async def service_account_for(self, tenant_slug: str, audience: str) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            "SELECT client_id, secret_hash, tenant_slug, audience, scopes, is_active "
            "FROM public.auth_service_accounts "
            "WHERE tenant_slug = $1 AND audience = $2 AND is_active "
            "ORDER BY created_at LIMIT 1",
            tenant_slug,
            audience,
        )
