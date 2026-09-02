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
from collections.abc import Sequence

import asyncpg

from app.core.constants import CADENA_NIVEL_AUTORITATIVO

_SOL_COLS = (
    "id, objeto_tipo, objeto_id, titulo, payload_sha256, estado, creado_por, "
    "nivel_seguridad, created_at, updated_at"
)

_CAD_COLS = (
    "id, solicitud_id, orden, firmante_id, rol, estado, firma_id, comentario, decidido_at"
)


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

    async def resolver_nivel_por_objeto(self, objeto_id: str) -> int | None:
        """Nivel de seguridad REAL del objeto referenciado, o ``None`` si no resuelve.

        **No recibe `objeto_tipo` a propósito.** Ese campo lo elige el cliente en el
        body, y usarlo para decidir *si* se consulta la fuente autoritativa convertía
        una llave de autorización en algo que el llamante podía apagar: bastaba
        escribir `"Radicado"`, o declarar `"borrador"` sobre el UUID de un radicado
        CLASIFICADA, para que el gate no-read-up se evaluara contra un nivel inventado.
        Aquí se busca el identificador en las tablas dueñas y se devuelve el nivel que
        se encuentre — el tipo declarado no interviene.

        Devuelve el **máximo** cuando el identificador coincide en más de una tabla
        (improbable, pero si ocurre el lado seguro es el nivel más alto). ``None``
        significa "no se pudo establecer" —no existe, o la tabla no está en este
        schema—, nunca "es público": quien llama decide si eso es un rechazo
        (`CADENA_NIVEL_AUTORITATIVO`) o un objeto sin nivel propio.

        La comparación doble (identidad natural / UUID) tolera que el llamante pase el
        número de radicado o el código del expediente en vez del UUID.
        """
        # Nombres de tabla y columna salen de la constante del módulo, jamás del
        # request. Una sola consulta en el caso normal (ambas tablas presentes).
        selects = [
            f"SELECT nivel_seguridad FROM {table} WHERE "
            + " OR ".join(f"{col} = $1" for col in id_cols)
            for table, id_cols in CADENA_NIVEL_AUTORITATIVO.values()
        ]
        try:
            return await self._conn.fetchval(
                f"SELECT MAX(nivel_seguridad) FROM ({' UNION ALL '.join(selects)}) AS n",
                objeto_id,
            )
        except asyncpg.UndefinedTableError:
            # Alguna tabla fuente falta en este schema (tenant sin document- o
            # archive-service, o entorno de test aislado): se reintenta tabla a
            # tabla para no perder las que SÍ están.
            return await self._resolver_nivel_por_tabla(objeto_id)

    async def _resolver_nivel_por_tabla(self, objeto_id: str) -> int | None:
        niveles: list[int] = []
        for table, id_cols in CADENA_NIVEL_AUTORITATIVO.values():
            where = " OR ".join(f"{col} = $1" for col in id_cols)
            try:
                nivel = await self._conn.fetchval(
                    f"SELECT nivel_seguridad FROM {table} WHERE {where} LIMIT 1",
                    objeto_id,
                )
            except asyncpg.UndefinedTableError:
                continue
            if nivel is not None:
                niveles.append(int(nivel))
        return max(niveles) if niveles else None

    async def get_solicitud(self, solicitud_id: uuid.UUID) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            f"SELECT {_SOL_COLS} FROM firma_solicitud WHERE id = $1",
            solicitud_id,
        )

    async def get_solicitud_for_update(self, solicitud_id: uuid.UUID) -> asyncpg.Record | None:
        """Same as get_solicitud but with a row lock (E06 Inc.2, RF-FIR-13).

        Used by CadenaFirmaService.emitir_challenge to hold the solicitud stable
        for the duration of the challenge-emission transaction (TOCTOU guard,
        same pattern as turno_activo_for_update).
        """
        return await self._conn.fetchrow(
            f"SELECT {_SOL_COLS} FROM firma_solicitud WHERE id = $1 FOR UPDATE",
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
        self,
        solicitud_id: uuid.UUID,
        firmantes: list[uuid.UUID],
        roles: Sequence[str] | None = None,
    ) -> None:
        """Inserta los turnos de la cadena (orden 1..N).

        `roles` es paralelo a `firmantes`. Cuando falta, todos los turnos son
        de `firma`: es lo que eran todas las cadenas antes de E23, y un default
        distinto reescribiría su significado en silencio.
        """
        for indice, (orden, firmante_id) in enumerate(enumerate(firmantes, start=1)):
            rol = roles[indice] if roles else "firma"
            await self._conn.execute(
                "INSERT INTO firma_cadena (solicitud_id, orden, firmante_id, rol) "
                "VALUES ($1, $2, $3, $4)",
                solicitud_id,
                orden,
                firmante_id,
                rol,
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

    async def actualizar_nivel_seguridad(
        self, objeto_tipo: str, objeto_ids: list[str], nivel: int
    ) -> int:
        """Syncs nivel_seguridad in firma_solicitud after a radicado reclassification.

        Updates all solicitudes matching objeto_tipo and any of the provided
        objeto_ids (typically [tracking_number, radicado_uuid_str]) whose stored
        nivel_seguridad differs from ``nivel``.  The ``nivel_seguridad <> $1``
        predicate makes this idempotent: redelivered events produce 0 updates.

        Returns the number of rows actually updated (for audit/logging).

        Tolerates asyncpg.UndefinedTableError (tenant schema without firma_solicitud)
        by returning 0 — a legitimate no-op (ackeable): the tenant has no signature
        chains to sync, so the event is safely consumed without re-queuing.
        This mirrors resolver_nivel_radicado's pattern for the radicados table.
        """
        try:
            tag: str = await self._conn.execute(
                "UPDATE firma_solicitud "
                "SET nivel_seguridad = $1, updated_at = now() "
                "WHERE objeto_tipo = $2 AND objeto_id = ANY($3::text[]) "
                "  AND nivel_seguridad <> $1",
                nivel,
                objeto_tipo,
                objeto_ids,
            )
            # asyncpg returns a command tag like "UPDATE 2"; parse the row count.
            return int(tag.split()[-1])
        except asyncpg.UndefinedTableError:
            # firma_solicitud table absent in this tenant schema.
            # No signature chains to sync — treat as 0 rows updated (ackeable no-op).
            return 0

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
                -- Qué le piden a esta persona, no solo que le piden algo: la
                -- bandeja enseña «Visto bueno» o «Firma» ANTES de abrir el
                -- detalle. Dar el visto bueno y firmar comprometen distinto.
                c.rol,
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
