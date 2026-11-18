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

_COLS = "id, radicado_id, anexo_id, signer_id, signer_name, content_hash, reason, signed_at, origen"


class SignatureRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def radicado_visible(self, radicado_id: uuid.UUID, max_clearance: int) -> bool:
        """¿El radicado existe Y está a-nivel-o-por-debajo del clearance? (RF-SEG-08).

        Un `False` cubre indistintamente «no existe» y «te supera», de modo que el
        router pueda devolver un 404 neutro en ambos casos (no-oráculo).
        """
        return bool(
            await self._conn.fetchval(
                "SELECT EXISTS (SELECT 1 FROM radicados "
                "WHERE id = $1 AND nivel_seguridad <= $2)",
                radicado_id,
                max_clearance,
            )
        )

    async def anexo_file_id(
        self, radicado_id: uuid.UUID, anexo_id: uuid.UUID, max_clearance: int
    ) -> uuid.UUID | None:
        """`file_id` del anexo SI pertenece al radicado y este es visible.

        `None` cubre «no existe», «es de otro radicado» y «te supera»: 404 neutro.
        """
        return await self._conn.fetchval(
            "SELECT a.file_id FROM anexos a JOIN radicados r ON r.id = a.radicado_id "
            "WHERE a.id = $1 AND a.radicado_id = $2 AND r.nivel_seguridad <= $3",
            anexo_id,
            radicado_id,
            max_clearance,
        )

    async def insert(
        self,
        *,
        radicado_id: uuid.UUID,
        anexo_id: uuid.UUID | None,
        signer_id: uuid.UUID | None,
        signer_name: str | None,
        content_hash: str,
        reason: str | None,
        max_clearance: int,
    ) -> asyncpg.Record | None:
        """Inserta la firma solo si el radicado es visible para el clearance dado.

        El gate va como `WHERE EXISTS` del propio INSERT (no como consulta previa):
        firmar es un acto probatorio, así que la comprobación y la escritura deben
        ser atómicas — una reclasificación concurrente no puede colarse entre ambas.
        Devuelve `None` si el radicado no existe o supera el clearance.
        """
        row = await self._conn.fetchrow(
            f"""
            INSERT INTO signatures
                (radicado_id, anexo_id, signer_id, signer_name, content_hash, reason, origen)
            SELECT $1, $2, $3, $4, $5, $6, 'servidor'
            WHERE EXISTS (
                SELECT 1 FROM radicados WHERE id = $1 AND nivel_seguridad <= $7
            )
            RETURNING {_COLS}
            """,
            radicado_id,
            anexo_id,
            signer_id,
            signer_name,
            content_hash,
            reason,
            max_clearance,
        )
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
        """Firmas cuya huella coincide Y fue resuelta por el servidor.

        Las heredadas con huella del cliente (`cliente_no_verificado`) no
        verifican: nadie comprobó qué se firmó.
        """
        return list(
            await self._conn.fetch(
                f"SELECT {_COLS} FROM signatures "
                "WHERE radicado_id = $1 AND content_hash = $2 AND origen = 'servidor' "
                "ORDER BY signed_at ASC",
                radicado_id,
                content_hash,
            )
        )
