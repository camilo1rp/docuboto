# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Repositorio de ``firma_challenge`` — 2FA por OTP-correo (E06 Inc.2, RF-FIR-13).

La conexión llega con ``search_path`` ya fijado al tenant (via
``get_tenant_conn``), igual que ``CadenaRepository``. Todos los métodos que
mutan estado (``expirar``, ``marcar_estado``, ``registrar_intento_fallido``,
``consumir``) deben invocarse dentro de la transacción que ya tiene el
challenge bloqueado con ``get_for_update`` — ninguno vuelve a adquirir el
lock ni condiciona el ``WHERE`` por estado, porque el lock de fila YA es la
garantía anti-TOCTOU (una segunda solicitud concurrente con el mismo
``challenge_id`` bloquea en ``get_for_update`` hasta que esta transacción
termine).
"""

from __future__ import annotations

import uuid
from datetime import datetime

import asyncpg

_COLS = (
    "challenge_id, solicitud_id, firmante_id, hash_documento, factor_tipo, otp_hash, "
    "estado, intentos, max_intentos, expira_at, verificado_at, consumido_at, creado_at, "
    "canal_masked, tenant_slug"
)


class ChallengeRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def get_activo(
        self,
        solicitud_id: uuid.UUID,
        firmante_id: uuid.UUID,
        hash_documento: str,
    ) -> asyncpg.Record | None:
        """Returns the live challenge (pendiente|verificado) for this triple, if any.

        Locked (FOR UPDATE) so the caller can safely expire-and-replace it within
        the same transaction (idempotent reissue, RF-FIR-13 Q1).
        """
        return await self._conn.fetchrow(
            f"""SELECT {_COLS} FROM firma_challenge
                WHERE solicitud_id = $1 AND firmante_id = $2 AND hash_documento = $3
                  AND estado IN ('pendiente', 'verificado')
                ORDER BY creado_at DESC
                LIMIT 1
                FOR UPDATE""",
            solicitud_id,
            firmante_id,
            hash_documento,
        )

    async def expirar(self, challenge_id: uuid.UUID) -> None:
        """Marks a challenge as 'expirado' — used to replace an active one on reissue."""
        await self._conn.execute(
            "UPDATE firma_challenge SET estado = 'expirado' WHERE challenge_id = $1",
            challenge_id,
        )

    async def crear(
        self,
        *,
        solicitud_id: uuid.UUID,
        firmante_id: uuid.UUID,
        hash_documento: str,
        otp_hash: str,
        max_intentos: int,
        expira_at: datetime,
        canal_masked: str,
        tenant_slug: str,
    ) -> asyncpg.Record:
        row = await self._conn.fetchrow(
            f"""INSERT INTO firma_challenge
                    (solicitud_id, firmante_id, hash_documento, otp_hash,
                     max_intentos, expira_at, canal_masked, tenant_slug)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                RETURNING {_COLS}""",
            solicitud_id,
            firmante_id,
            hash_documento,
            otp_hash,
            max_intentos,
            expira_at,
            canal_masked,
            tenant_slug,
        )
        assert row is not None
        return row

    async def get_for_update(self, challenge_id: uuid.UUID) -> asyncpg.Record | None:
        """Locks the challenge row for the verify-and-consume gate in POST /sign."""
        return await self._conn.fetchrow(
            f"SELECT {_COLS} FROM firma_challenge WHERE challenge_id = $1 FOR UPDATE",
            challenge_id,
        )

    async def marcar_estado(self, challenge_id: uuid.UUID, estado: str) -> None:
        """Transitions to a terminal state with no attempt-count change (expirado/bloqueado)."""
        await self._conn.execute(
            "UPDATE firma_challenge SET estado = $2 WHERE challenge_id = $1",
            challenge_id,
            estado,
        )

    async def registrar_intento_fallido(
        self, challenge_id: uuid.UUID, *, intentos: int, estado: str
    ) -> None:
        """Persists a failed OTP attempt: bumps `intentos`, optionally to 'bloqueado'.

        Must be committed durably by the caller even when the enclosing sign
        attempt is aborted (RF-FIR-13) — see CadenaFirmaService._gate_2fa.
        """
        await self._conn.execute(
            "UPDATE firma_challenge SET intentos = $2, estado = $3 WHERE challenge_id = $1",
            challenge_id,
            intentos,
            estado,
        )

    async def consumir(self, challenge_id: uuid.UUID, consumido_at: datetime) -> None:
        """Marks the challenge as 'consumido' — terminal, anti-replay.

        ``consumido_at`` is supplied by the caller (not the DB's ``now()``) so
        the persisted timestamp is byte-identical to the one the caller
        embeds in the ``firma.turno_firmado`` audit payload — a single
        authoritative instant for both records (RF-FIR-13/14 evidentiary
        link), see ``CadenaFirmaService._gate_2fa``.
        """
        await self._conn.execute(
            "UPDATE firma_challenge SET estado = 'consumido', consumido_at = $2 "
            "WHERE challenge_id = $1",
            challenge_id,
            consumido_at,
        )
