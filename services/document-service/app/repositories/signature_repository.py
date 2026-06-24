# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Firmas electrónicas (E06, ADR-016).

Inserta y consulta firmas; la conexión llega con search_path al tenant. La
verificación se resuelve listando las firmas cuyo `content_hash` coincide.
"""

from __future__ import annotations

import uuid

import asyncpg

_COLS = "id, radicado_id, anexo_id, signer_id, signer_name, content_hash, reason, signed_at"


class SignatureRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def insert(
        self,
        *,
        radicado_id: uuid.UUID,
        anexo_id: uuid.UUID | None,
        signer_id: uuid.UUID | None,
        signer_name: str | None,
        content_hash: str,
        reason: str | None,
    ) -> asyncpg.Record:
        row = await self._conn.fetchrow(
            f"""
            INSERT INTO signatures
                (radicado_id, anexo_id, signer_id, signer_name, content_hash, reason)
            VALUES ($1, $2, $3, $4, $5, $6)
            RETURNING {_COLS}
            """,
            radicado_id,
            anexo_id,
            signer_id,
            signer_name,
            content_hash,
            reason,
        )
        assert row is not None
        return row

    async def list_by_radicado(self, radicado_id: uuid.UUID) -> list[asyncpg.Record]:
        return list(
            await self._conn.fetch(
                f"SELECT {_COLS} FROM signatures WHERE radicado_id = $1 " "ORDER BY signed_at ASC",
                radicado_id,
            )
        )

    async def list_matching(
        self, radicado_id: uuid.UUID, content_hash: str
    ) -> list[asyncpg.Record]:
        return list(
            await self._conn.fetch(
                f"SELECT {_COLS} FROM signatures "
                "WHERE radicado_id = $1 AND content_hash = $2 ORDER BY signed_at ASC",
                radicado_id,
                content_hash,
            )
        )
