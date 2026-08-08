# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Acceso a datos de ``auth_user_totp`` (E06, RFC 6238). SQL crudo; la tabla
vive en el schema del tenant (search_path fijado por get_tenant_conn / el
switch manual del endpoint interno de verificación)."""

from __future__ import annotations

import uuid

import asyncpg

_COLS = (
    "id, user_id, secret_cifrado, secret_nonce, algoritmo, digitos, periodo, "
    "estado, last_timestep, failed_attempts, locked_until, key_version, "
    "created_at, activated_at, updated_at"
)


class TotpRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def get_by_user(self, user_id: uuid.UUID) -> asyncpg.Record | None:
        """The single non-revoked credential (pendiente_activacion|activo), if any."""
        return await self._conn.fetchrow(
            f"SELECT {_COLS} FROM auth_user_totp "
            "WHERE user_id = $1 AND estado <> 'revocado'",
            user_id,
        )

    async def insert_pending(
        self,
        *,
        user_id: uuid.UUID,
        secret_cifrado: bytes,
        secret_nonce: bytes,
        key_version: int,
    ) -> asyncpg.Record:
        row = await self._conn.fetchrow(
            f"""INSERT INTO auth_user_totp (user_id, secret_cifrado, secret_nonce, key_version)
                VALUES ($1, $2, $3, $4)
                RETURNING {_COLS}""",
            user_id,
            secret_cifrado,
            secret_nonce,
            key_version,
        )
        assert row is not None
        return row

    async def revoke(self, totp_id: uuid.UUID) -> None:
        await self._conn.execute(
            "UPDATE auth_user_totp SET estado = 'revocado', updated_at = now() WHERE id = $1",
            totp_id,
        )

    # ------------------------------------------------------------------
    # Códigos de recuperación (migración 015)
    # ------------------------------------------------------------------

    async def replace_recovery_codes(
        self, *, totp_id: uuid.UUID, user_id: uuid.UUID, hashes: list[tuple[bytes, bytes]]
    ) -> None:
        """Sustituye TODO el lote de códigos del usuario por ``hashes``
        (pares ``(code_hash, code_salt)``).

        Borra primero **todos** los del usuario, no solo los de este `totp_id`:
        un reenrolamiento crea una fila TOTP nueva, y los códigos de la anterior
        deben morir con ella aunque su fila quede marcada 'revocado' en vez de
        borrarse (el `ON DELETE CASCADE` de la 015 no se dispara en ese caso).
        Un código que sobreviviera al reenrolamiento abriría una credencial que
        el usuario ya dio por retirada.

        El llamante lo envuelve en la MISMA transacción que la mutación del
        TOTP — no se expone una ventana en la que el usuario tenga códigos que
        no abren nada, ni al revés.
        """
        await self._conn.execute(
            "DELETE FROM auth_user_totp_recovery WHERE user_id = $1", user_id
        )
        for code_hash, code_salt in hashes:
            await self._conn.execute(
                """INSERT INTO auth_user_totp_recovery
                       (totp_id, user_id, code_hash, code_salt)
                   VALUES ($1, $2, $3, $4)""",
                totp_id,
                user_id,
                code_hash,
                code_salt,
            )

    async def delete_recovery_codes(self, user_id: uuid.UUID) -> None:
        await self._conn.execute(
            "DELETE FROM auth_user_totp_recovery WHERE user_id = $1", user_id
        )

    async def list_available_recovery_codes(self, user_id: uuid.UUID) -> list[asyncpg.Record]:
        return list(
            await self._conn.fetch(
                "SELECT id, code_hash, code_salt FROM auth_user_totp_recovery "
                "WHERE user_id = $1 AND used_at IS NULL",
                user_id,
            )
        )

    async def count_available_recovery_codes(self, user_id: uuid.UUID) -> int:
        n = await self._conn.fetchval(
            "SELECT count(*) FROM auth_user_totp_recovery "
            "WHERE user_id = $1 AND used_at IS NULL",
            user_id,
        )
        return int(n or 0)

    async def consume_recovery_code(self, recovery_id: uuid.UUID) -> bool:
        """Marca un código como usado. Devuelve ``False`` si ya lo estaba.

        El `WHERE used_at IS NULL` es la parte que importa: dos peticiones
        concurrentes con el mismo código compiten por esta fila y solo una ve
        el `RETURNING`; la otra recibe `False` y trata el código como
        inválido. Sin esa condición, un código de un solo uso se gastaría dos
        veces bajo carrera.
        """
        row = await self._conn.fetchrow(
            "UPDATE auth_user_totp_recovery SET used_at = now() "
            "WHERE id = $1 AND used_at IS NULL RETURNING id",
            recovery_id,
        )
        return row is not None

    async def activate(self, totp_id: uuid.UUID) -> None:
        await self._conn.execute(
            "UPDATE auth_user_totp SET estado = 'activo', activated_at = now(), "
            "updated_at = now(), failed_attempts = 0 WHERE id = $1",
            totp_id,
        )

    async def advance_timestep(self, user_id: uuid.UUID, *, new_timestep: int) -> bool:
        """Atomic anti-replay advance: a single conditional UPDATE.

        ``WHERE ... AND last_timestep < new_timestep`` — a concurrent verify
        that already advanced past ``new_timestep`` makes this a no-op, and
        the caller must treat that as a lost race (replay), never as success.
        Also resets ``failed_attempts`` (a valid code clears the counter).
        """
        result = await self._conn.execute(
            "UPDATE auth_user_totp SET last_timestep = $2, failed_attempts = 0, "
            "updated_at = now() "
            "WHERE user_id = $1 AND estado <> 'revocado' AND last_timestep < $2",
            user_id,
            new_timestep,
        )
        return result == "UPDATE 1"

    async def record_failure(
        self,
        totp_id: uuid.UUID,
        *,
        max_attempts: int,
        lockout_seconds: int,
    ) -> asyncpg.Record:
        """Atomically increments ``failed_attempts`` and derives ``locked_until``
        in a SINGLE server-side ``UPDATE ... RETURNING`` — never a Python
        read-modify-write. Under concurrent failures (e.g. the internal
        ``/totp/verify`` endpoint, which isn't serialized by signature-
        service's turno ``FOR UPDATE``), N concurrent callers each doing
        "read failed_attempts, compute +1, write absolute value" would all
        read the same base and all write ``base + 1`` — losing N-1 of the
        increments and computing ``locked_until`` from a stale count. Doing
        ``failed_attempts + 1`` and the lockout decision inside the same
        UPDATE makes Postgres's row lock the serialization point instead.
        Callers must derive ``intentos_restantes``/``locked_until`` from the
        RETURNING row, never from a previously-read one."""
        row = await self._conn.fetchrow(
            "UPDATE auth_user_totp SET failed_attempts = failed_attempts + 1, "
            "locked_until = CASE WHEN failed_attempts + 1 >= $2 "
            "THEN now() + make_interval(secs => $3) ELSE locked_until END, "
            "updated_at = now() "
            "WHERE id = $1 "
            "RETURNING failed_attempts, locked_until",
            totp_id,
            max_attempts,
            lockout_seconds,
        )
        assert row is not None
        return row
