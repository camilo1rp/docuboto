# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Registro de retenciones legales por MOTIVO (C-5/C7, ADR-043, migración 024).

Una fila de ``preservacion_legal_hold`` por acto de fijar. El estado S3 de un objeto («hold puesto»)
es «el expediente tiene >= 1 hold activo»; este repositorio solo escribe el registro, el
``PreservacionWormRepository`` escribe el espejo en cada fila de objeto y el servicio libera S3
únicamente cuando el recuento llega a cero.

search_path al tenant en cada método (gemelo de ``PreservacionWormRepository``).
"""

from __future__ import annotations

import uuid

import asyncpg
from orpycamcp_common.tenant import tenant_schema

_COLS = (
    "id, expediente_id, motivo_tipo, fundamento, acta_ref, fijado_por, fijado_at, "
    "levantado_por, levantado_at, levantado_fundamento, levantado_acta_ref"
)


class LegalHoldRepository:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._schema = tenant_schema(tenant_slug)

    async def _sp(self) -> None:
        await self._conn.execute(f'SET search_path = "{self._schema}"')

    async def insert(
        self,
        *,
        expediente_id: str,
        motivo_tipo: str,
        fundamento: str,
        acta_ref: str,
        fijado_por: uuid.UUID | None,
    ) -> asyncpg.Record:
        await self._sp()
        row = await self._conn.fetchrow(
            f"""
            INSERT INTO preservacion_legal_hold
                (expediente_id, motivo_tipo, fundamento, acta_ref, fijado_por)
            VALUES ($1, $2, $3, $4, $5)
            RETURNING {_COLS}
            """,
            expediente_id,
            motivo_tipo,
            fundamento,
            acta_ref,
            fijado_por,
        )
        assert row is not None
        return row

    async def get(self, hold_id: uuid.UUID) -> asyncpg.Record | None:
        await self._sp()
        return await self._conn.fetchrow(
            f"SELECT {_COLS} FROM preservacion_legal_hold WHERE id = $1", hold_id
        )

    async def valoracion_activa(self, expediente_id: str) -> asyncpg.Record | None:
        """El hold de sistema `valoracion_pendiente` ACTIVO del expediente (a lo sumo uno:
        `ux_plh_valoracion_activa`)."""
        await self._sp()
        return await self._conn.fetchrow(
            f"SELECT {_COLS} FROM preservacion_legal_hold "
            "WHERE expediente_id = $1 AND motivo_tipo = 'valoracion_pendiente' "
            "AND levantado_at IS NULL",
            expediente_id,
        )

    async def contar_activos_excepto(self, expediente_id: str, hold_id: uuid.UUID) -> int:
        """Holds activos del expediente SIN contar `hold_id`: lo que queda tras levantarlo."""
        await self._sp()
        rows = await self._conn.fetch(
            "SELECT id FROM preservacion_legal_hold "
            "WHERE expediente_id = $1 AND levantado_at IS NULL AND id <> $2",
            expediente_id,
            hold_id,
        )
        return len(rows)

    async def levantar(
        self,
        hold_id: uuid.UUID,
        *,
        levantado_por: uuid.UUID | None,
        fundamento: str,
        acta_ref: str,
    ) -> asyncpg.Record | None:
        """Activo -> levantado. `None` si ya estaba levantado (el trigger tampoco lo permitiría)."""
        await self._sp()
        return await self._conn.fetchrow(
            f"""
            UPDATE preservacion_legal_hold
            SET levantado_por = $2, levantado_at = now(),
                levantado_fundamento = $3, levantado_acta_ref = $4
            WHERE id = $1 AND levantado_at IS NULL
            RETURNING {_COLS}
            """,
            hold_id,
            levantado_por,
            fundamento,
            acta_ref,
        )

    async def list_for_expediente(self, expediente_id: str) -> list[asyncpg.Record]:
        await self._sp()
        return list(
            await self._conn.fetch(
                f"SELECT {_COLS} FROM preservacion_legal_hold "
                "WHERE expediente_id = $1 ORDER BY fijado_at, id",
                expediente_id,
            )
        )
