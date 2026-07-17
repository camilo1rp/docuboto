# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Acceso a datos de ``auth_user_signing_key`` (E17/F4, Incremento 1). SQL
crudo; la tabla vive en el schema del tenant (search_path fijado por la
conexión — mismo patrón que ``TotpRepository``)."""

from __future__ import annotations

import uuid

import asyncpg

_COLS = (
    "id, user_id, cert_pem, csr_pem, private_key_cifrado, private_key_nonce, "
    "subject_dn, cargo, serial, not_before, not_after, key_version, estado, "
    "created_at, activated_at, updated_at"
)


class SigningKeyRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def get_by_user(self, user_id: uuid.UUID) -> asyncpg.Record | None:
        """La única credencial no-revocada (pendiente_emision|activo), si la hay."""
        return await self._conn.fetchrow(
            f"SELECT {_COLS} FROM auth_user_signing_key "
            "WHERE user_id = $1 AND estado <> 'revocado'",
            user_id,
        )

    async def get_by_user_for_update(self, user_id: uuid.UUID) -> asyncpg.Record | None:
        """Misma consulta que :meth:`get_by_user`, bajo ``FOR UPDATE`` — usado
        por :meth:`SigningKeyService.load_and_sign_digest` (debe llamarse
        DENTRO de una transacción) para serializar contra un re-enroll
        concurrente mientras se firma."""
        return await self._conn.fetchrow(
            f"SELECT {_COLS} FROM auth_user_signing_key "
            "WHERE user_id = $1 AND estado <> 'revocado' FOR UPDATE",
            user_id,
        )

    async def insert_pending(
        self,
        *,
        user_id: uuid.UUID,
        csr_pem: str,
        private_key_cifrado: bytes,
        private_key_nonce: bytes,
        subject_dn: str,
        cargo: str | None,
        key_version: int,
    ) -> asyncpg.Record:
        row = await self._conn.fetchrow(
            f"""INSERT INTO auth_user_signing_key
                    (user_id, csr_pem, private_key_cifrado, private_key_nonce,
                     subject_dn, cargo, key_version)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING {_COLS}""",
            user_id,
            csr_pem,
            private_key_cifrado,
            private_key_nonce,
            subject_dn,
            cargo,
            key_version,
        )
        assert row is not None
        return row

    async def revoke(self, key_id: uuid.UUID) -> None:
        await self._conn.execute(
            "UPDATE auth_user_signing_key SET estado = 'revocado', updated_at = now() "
            "WHERE id = $1",
            key_id,
        )

    async def activate(
        self,
        key_id: uuid.UUID,
        *,
        cert_pem: str,
        serial: str,
        not_before,
        not_after,
    ) -> asyncpg.Record:
        row = await self._conn.fetchrow(
            f"""UPDATE auth_user_signing_key
                SET estado = 'activo', cert_pem = $2, serial = $3,
                    not_before = $4, not_after = $5, activated_at = now(),
                    updated_at = now()
                WHERE id = $1
                RETURNING {_COLS}""",
            key_id,
            cert_pem,
            serial,
            not_before,
            not_after,
        )
        assert row is not None
        return row
