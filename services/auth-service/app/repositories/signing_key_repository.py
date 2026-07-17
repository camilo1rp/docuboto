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
    "created_at, activated_at, updated_at, "
    "revocado_at, revocado_por, motivo_revocacion, certificado_fingerprint, "
    "user_ca_fingerprint"
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
        certificado_fingerprint: str,
        user_ca_fingerprint: str,
    ) -> asyncpg.Record:
        """``certificado_fingerprint``/``user_ca_fingerprint`` (Incremento 2,
        Baja #4): la huella del leaf y la de la sub-CA que lo emitió,
        verificada por el llamante (``SigningKeyService.activar_certificado``)
        ANTES de esta escritura vía ``leaf.verify_directly_issued_by(sub_ca)``
        — aquí solo se persisten, ya calculadas."""
        row = await self._conn.fetchrow(
            f"""UPDATE auth_user_signing_key
                SET estado = 'activo', cert_pem = $2, serial = $3,
                    not_before = $4, not_after = $5, activated_at = now(),
                    updated_at = now(), certificado_fingerprint = $6,
                    user_ca_fingerprint = $7
                WHERE id = $1
                RETURNING {_COLS}""",
            key_id,
            cert_pem,
            serial,
            not_before,
            not_after,
            certificado_fingerprint,
            user_ca_fingerprint,
        )
        assert row is not None
        return row

    async def revoke_atomic(
        self,
        key_id: uuid.UUID,
        *,
        revocado_por: uuid.UUID,
        motivo: str | None,
    ) -> asyncpg.Record:
        """Revocación explícita (self-revoke / admin-revoke, Incremento 2) —
        DISTINTA de :meth:`revoke` (usada por ``enroll`` al reemplazar una
        credencial en un re-enroll, sin estos metadatos). Registra
        ``revocado_at``/``revocado_por``/``motivo_revocacion`` — el llamante
        (``SigningKeyService.revoke_self``/``revoke_admin``) es responsable
        de emitir el asiento ``signing_key.revocada`` DENTRO de la MISMA
        transacción que este UPDATE (patrón fail-closed, igual que
        ``activate``/``activar_certificado`` — nunca el best-effort de
        ``enroll``)."""
        row = await self._conn.fetchrow(
            f"""UPDATE auth_user_signing_key
                SET estado = 'revocado', revocado_at = now(), revocado_por = $2,
                    motivo_revocacion = $3, updated_at = now()
                WHERE id = $1
                RETURNING {_COLS}""",
            key_id,
            revocado_por,
            motivo,
        )
        assert row is not None
        return row

    async def get_by_certificado_fingerprint(self, fingerprint: str) -> asyncpg.Record | None:
        """Resuelve el estado de revocación por huella del certificado LEAF —
        usado por ``GET /internal/signing-key/revocation-status``
        (signature-service, D-02). A diferencia de :meth:`get_by_user`, NO
        filtra por ``estado`` (necesita encontrar filas YA 'revocado' para
        reportarlas como tales) — el ``search_path`` de la conexión ya acota
        la búsqueda al tenant del header ``X-Tenant-Slug`` (fail-closed por
        aislamiento: una huella de otro tenant simplemente no aparece aquí).
        Si hubiera más de una fila histórica con la MISMA huella (no debería,
        cada clave privada es única), se prefiere la más reciente."""
        return await self._conn.fetchrow(
            f"SELECT {_COLS} FROM auth_user_signing_key "
            "WHERE certificado_fingerprint = $1 "
            "ORDER BY created_at DESC LIMIT 1",
            fingerprint,
        )
