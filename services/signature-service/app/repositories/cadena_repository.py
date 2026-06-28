# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Repositorio de cadena de firma (firma_solicitud + firma_cadena).

La conexión llega con search_path ya fijado al tenant (via get_tenant_conn).
NO se vuelve a fijar search_path aquí (a diferencia de VistoBuenoRepository,
que usa get_db_conn sin tenant). Todos los placeholders usan $N de asyncpg.
"""

from __future__ import annotations

import uuid

import asyncpg

_SOL_COLS = (
    "id, objeto_tipo, objeto_id, titulo, payload_sha256, estado, creado_por, "
    "nivel_seguridad, created_at, updated_at"
)

_CAD_COLS = "id, solicitud_id, orden, firmante_id, estado, firma_id, comentario, decidido_at"


class CadenaRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------------
    # firma_solicitud
    # ------------------------------------------------------------------

    async def crear_solicitud(self, data: dict) -> asyncpg.Record:
        row = await self._conn.fetchrow(
            f"""INSERT INTO firma_solicitud
                    (objeto_tipo, objeto_id, titulo, payload_sha256, creado_por, nivel_seguridad)
                VALUES ($1, $2, $3, $4, $5, $6)
                RETURNING {_SOL_COLS}""",
            data["objeto_tipo"],
            data.get("objeto_id"),
            data.get("titulo"),
            data.get("payload_sha256"),
            data.get("creado_por"),
            data.get("nivel_seguridad", 1),
        )
        assert row is not None
        return row

    async def resolver_nivel_radicado(self, objeto_id: str) -> int | None:
        """Returns nivel_seguridad for a radicado matched by tracking_number or UUID text.

        Returns None when no radicado matches (the object_id may be a different type,
        or the radicados table may be absent in test/isolated environments).
        The OR condition tolerates callers passing either a tracking number
        (e.g. '2024-DEMO-E-000001') or the radicado's UUID as text.
        """
        try:
            return await self._conn.fetchval(
                "SELECT nivel_seguridad FROM radicados "
                "WHERE tracking_number = $1 OR id::text = $1 LIMIT 1",
                objeto_id,
            )
        except asyncpg.UndefinedTableError:
            # radicados table absent in this tenant schema (test or isolated env).
            return None

    async def get_solicitud(self, solicitud_id: uuid.UUID) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            f"SELECT {_SOL_COLS} FROM firma_solicitud WHERE id = $1",
            solicitud_id,
        )

    async def get_solicitud_por_objeto(
        self,
        objeto_tipo: str,
        objeto_id: str,
        solo_abierta: bool = True,
    ) -> asyncpg.Record | None:
        if solo_abierta:
            return await self._conn.fetchrow(
                f"SELECT {_SOL_COLS} FROM firma_solicitud "
                "WHERE objeto_tipo = $1 AND objeto_id = $2 AND estado = 'pendiente'",
                objeto_tipo,
                objeto_id,
            )
        return await self._conn.fetchrow(
            f"SELECT {_SOL_COLS} FROM firma_solicitud "
            "WHERE objeto_tipo = $1 AND objeto_id = $2 ORDER BY created_at DESC LIMIT 1",
            objeto_tipo,
            objeto_id,
        )

    async def existe_abierta(self, objeto_tipo: str, objeto_id: str) -> bool:
        val = await self._conn.fetchval(
            "SELECT 1 FROM firma_solicitud "
            "WHERE objeto_tipo = $1 AND objeto_id = $2 AND estado = 'pendiente' LIMIT 1",
            objeto_tipo,
            objeto_id,
        )
        return val is not None

    async def actualizar_estado_solicitud(
        self, solicitud_id: uuid.UUID, estado: str
    ) -> None:
        await self._conn.execute(
            "UPDATE firma_solicitud SET estado = $2, updated_at = now() WHERE id = $1",
            solicitud_id,
            estado,
        )

    # ------------------------------------------------------------------
    # firma_cadena
    # ------------------------------------------------------------------

    async def crear_turnos(
        self, solicitud_id: uuid.UUID, firmantes: list[uuid.UUID]
    ) -> None:
        """Inserts all chain turns (orden 1..N) for the given solicitud."""
        for orden, firmante_id in enumerate(firmantes, start=1):
            await self._conn.execute(
                "INSERT INTO firma_cadena (solicitud_id, orden, firmante_id) "
                "VALUES ($1, $2, $3)",
                solicitud_id,
                orden,
                firmante_id,
            )

    async def list_turnos(self, solicitud_id: uuid.UUID) -> list[asyncpg.Record]:
        rows = await self._conn.fetch(
            f"SELECT {_CAD_COLS} FROM firma_cadena "
            "WHERE solicitud_id = $1 ORDER BY orden",
            solicitud_id,
        )
        return list(rows)

    async def turno_activo(self, solicitud_id: uuid.UUID) -> asyncpg.Record | None:
        """Returns the current active turn: lowest orden with estado='pendiente'."""
        return await self._conn.fetchrow(
            f"SELECT {_CAD_COLS} FROM firma_cadena "
            "WHERE solicitud_id = $1 AND estado = 'pendiente' ORDER BY orden LIMIT 1",
            solicitud_id,
        )

    async def turno_activo_for_update(
        self, solicitud_id: uuid.UUID
    ) -> asyncpg.Record | None:
        """
        Same as turno_activo but acquires a row-level lock (FOR UPDATE).

        Must be called inside a transaction. Prevents two concurrent requests
        from the same firmante signing the same turn (TOCTOU, RF-FIR-04):
        the second request blocks until the first transaction commits, at which
        point estado='firmado' and marcar_turno returns False (0 rows affected).
        """
        return await self._conn.fetchrow(
            f"SELECT {_CAD_COLS} FROM firma_cadena "
            "WHERE solicitud_id = $1 AND estado = 'pendiente' ORDER BY orden LIMIT 1 "
            "FOR UPDATE",
            solicitud_id,
        )

    async def es_firmante(
        self, solicitud_id: uuid.UUID, firmante_id: uuid.UUID
    ) -> bool:
        """Returns True if firmante_id is any member of this chain."""
        val = await self._conn.fetchval(
            "SELECT 1 FROM firma_cadena "
            "WHERE solicitud_id = $1 AND firmante_id = $2 LIMIT 1",
            solicitud_id,
            firmante_id,
        )
        return val is not None

    async def marcar_turno(
        self,
        turno_id: uuid.UUID,
        estado: str,
        firma_id: uuid.UUID | None = None,
        comentario: str | None = None,
    ) -> bool:
        """
        Marks a turn as resolved; returns True iff exactly 1 row was updated.

        The WHERE clause conditions on estado='pendiente' so that a concurrent
        transaction that already committed the same update yields 0 rows —
        the caller must treat False as a 409 (duplicate sign attempt).
        """
        tag: str = await self._conn.execute(
            "UPDATE firma_cadena "
            "SET estado = $2, firma_id = $3, comentario = $4, decidido_at = now() "
            "WHERE id = $1 AND estado = 'pendiente'",
            turno_id,
            estado,
            firma_id,
            comentario,
        )
        # asyncpg returns the command tag string, e.g. "UPDATE 1" or "UPDATE 0".
        return tag.split()[-1] == "1"

    async def pendientes_de_firmante(
        self,
        firmante_id: uuid.UUID,
        offset: int,
        limit: int,
        max_clearance: int = 1,
    ) -> tuple[list[asyncpg.Record], int]:
        """
        Returns (rows, total) for the inbox: solicitudes where the ACTIVE turn
        (lowest pending orden) belongs to firmante_id, filtered by max_clearance.

        The nivel_seguridad <= max_clearance predicate enforces no-read-up (RF-SEG-08):
        the window COUNT(*) OVER() applies the same filter so X-Total-Count does not
        reveal the number of hidden solicitudes above the actor's clearance.

        Each row contains: solicitud_id, objeto_tipo, objeto_id, titulo, orden, created_at.
        """
        rows = await self._conn.fetch(
            """
            SELECT
                s.id            AS solicitud_id,
                s.objeto_tipo,
                s.objeto_id,
                s.titulo,
                c.orden,
                s.created_at,
                COUNT(*) OVER() AS total_count
            FROM firma_solicitud s
            JOIN firma_cadena c ON c.solicitud_id = s.id
            WHERE s.estado          = 'pendiente'
              AND c.firmante_id     = $1
              AND c.estado          = 'pendiente'
              AND s.nivel_seguridad <= $4
              AND c.orden = (
                  SELECT MIN(c2.orden)
                  FROM firma_cadena c2
                  WHERE c2.solicitud_id = s.id AND c2.estado = 'pendiente'
              )
            ORDER BY s.created_at DESC
            LIMIT $2 OFFSET $3
            """,
            firmante_id,
            limit,
            offset,
            max_clearance,
        )
        total = int(rows[0]["total_count"]) if rows else 0
        return list(rows), total
