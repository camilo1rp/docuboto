# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Repositorio de solicitudes de desclasificación (C4, ADR-039). SQL crudo; el
`search_path` ya viene fijado por la conexión.

Patrón de la separación de funciones: `archive_repository.avalar_lote_eliminacion`.
La guarda (estado, plazo, persona distinta, clearance) va en el `WHERE` de la MISMA
sentencia que la transición; `None` no distingue causas y el diagnóstico se compone
con una lectura aparte (`diagnostico`) que SOLO explica: nunca autoriza.
"""

from __future__ import annotations

import uuid

import asyncpg

#: Vigencia de una solicitud sin resolver. Está también como DEFAULT de la columna
#: `vence_at` (migración 050); aquí solo se documenta, el valor lo pone la base.
VIGENCIA_DIAS = 15

_ESTADO_EFECTIVO = (
    "CASE WHEN s.estado = 'pendiente' AND s.vence_at <= now() "
    "THEN 'caducada' ELSE s.estado END"
)

_COLS = (
    "s.id, s.radicado_id, s.nivel_anterior, s.nivel_nuevo, s.motivo, "
    "s.causal_codigo AS causal_reserva, s.fundamento_juridico, s.fundamento_norma, "
    "s.plazo_meses AS plazo_reserva_meses, s.estado, "
    f"{_ESTADO_EFECTIVO} AS estado_efectivo, "
    "s.solicitado_por, s.solicitado_at, s.vence_at, s.resuelto_por, s.resuelto_at, "
    "s.resolucion_motivo"
)


class ReclasificacionRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    #: Condición de la COLA: pendiente, vigente y de un radicado que el llamante
    #: ALCANZA. Una sola definición para las filas y para el total: si divergieran,
    #: la diferencia entre lo contado y lo mostrado sería la existencia de solicitudes
    #: sobre radicados clasificados que el llamante no puede ver.
    _VISIBLE_LISTA = "s.estado = 'pendiente' AND s.vence_at > now() AND r.nivel_seguridad <= $1"

    async def radicado_de(self, solicitud_id: uuid.UUID) -> uuid.UUID | None:
        """Radicado de una solicitud, SOLO para decidir qué fila bloquear.

        No autoriza nada ni sale al llamante: el acto vuelve a leer el radicado con
        el gate de clearance en el `WHERE` (`get_security_context_for_update`), y
        «no existe» y «no lo alcanza» producen el mismo 404.
        """
        return await self._conn.fetchval(
            "SELECT radicado_id FROM reclasificacion_solicitudes WHERE id = $1",
            solicitud_id,
        )

    async def caducar_vencidas(self, radicado_id: uuid.UUID) -> list[asyncpg.Record]:
        """Materializa `pendiente -> caducada` de lo vencido de ese radicado.

        Perezoso (sin job): las lecturas ya devuelven `estado_efectivo`, y esto
        libera el índice único de «una pendiente por radicado» al pedir de nuevo.
        Devuelve las filas caducadas para que el servicio asiente cada una.
        """
        return list(
            await self._conn.fetch(
                "UPDATE reclasificacion_solicitudes s SET estado = 'caducada' "
                "WHERE s.radicado_id = $1 AND s.estado = 'pendiente' AND s.vence_at <= now() "
                f"RETURNING {_COLS}",
                radicado_id,
            )
        )

    async def pendiente_de(self, radicado_id: uuid.UUID) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            f"SELECT {_COLS} FROM reclasificacion_solicitudes s "
            "WHERE s.radicado_id = $1 AND s.estado = 'pendiente'",
            radicado_id,
        )

    async def insertar(
        self,
        *,
        radicado_id: uuid.UUID,
        nivel_anterior: int,
        nivel_nuevo: int,
        motivo: str,
        causal: str | None,
        fundamento: str | None,
        plazo_meses: int | None,
        solicitado_por: uuid.UUID,
        norma: str | None = None,
    ) -> asyncpg.Record:
        return await self._conn.fetchrow(
            "INSERT INTO reclasificacion_solicitudes AS s (radicado_id, nivel_anterior, "
            "nivel_nuevo, motivo, causal_codigo, fundamento_juridico, plazo_meses, "
            "solicitado_por, fundamento_norma) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9) "
            f"RETURNING {_COLS}",
            radicado_id,
            nivel_anterior,
            nivel_nuevo,
            motivo,
            causal,
            fundamento,
            plazo_meses,
            solicitado_por,
            norma,
        )

    async def aprobar(
        self, solicitud_id: uuid.UUID, aprobador: uuid.UUID, max_clearance: int
    ) -> asyncpg.Record | None:
        """`pendiente -> aprobada`, con TODAS las guardas en el `WHERE`.

        Estado pendiente, vigente (`vence_at > now()`), persona distinta de quien
        pidió, y radicado en el nivel del que se pidió bajar Y dentro del clearance
        de quien aprueba. `None` no dice cuál falló (`diagnostico`). Aprobar no basta
        para bajar el nivel: el servicio lo hace en la misma transacción, y el
        trigger de frontera de `radicados` exige esta fila (`resuelto_at = now()`).
        """
        return await self._conn.fetchrow(
            "UPDATE reclasificacion_solicitudes s SET estado = 'aprobada', "
            "resuelto_por = $2, resuelto_at = now() "
            "WHERE s.id = $1 AND s.estado = 'pendiente' AND s.vence_at > now() "
            "AND s.solicitado_por <> $2 "
            "AND EXISTS (SELECT 1 FROM radicados r WHERE r.id = s.radicado_id "
            "            AND r.nivel_seguridad = s.nivel_anterior "
            "            AND r.nivel_seguridad <= $3) "
            f"RETURNING {_COLS}",
            solicitud_id,
            aprobador,
            max_clearance,
        )

    async def rechazar(
        self,
        solicitud_id: uuid.UUID,
        decisor: uuid.UUID,
        motivo: str,
        max_clearance: int,
    ) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            "UPDATE reclasificacion_solicitudes s SET estado = 'rechazada', "
            "resuelto_por = $2, resuelto_at = now(), resolucion_motivo = $3 "
            "WHERE s.id = $1 AND s.estado = 'pendiente' AND s.vence_at > now() "
            "AND s.solicitado_por <> $2 "
            "AND EXISTS (SELECT 1 FROM radicados r WHERE r.id = s.radicado_id "
            "            AND r.nivel_seguridad <= $4) "
            f"RETURNING {_COLS}",
            solicitud_id,
            decisor,
            motivo,
            max_clearance,
        )

    async def retirar(
        self,
        solicitud_id: uuid.UUID,
        solicitante: uuid.UUID,
        motivo: str | None,
        max_clearance: int,
    ) -> asyncpg.Record | None:
        """Solo quien pidió puede retirar (`solicitado_por = $2` en el `WHERE`)."""
        return await self._conn.fetchrow(
            "UPDATE reclasificacion_solicitudes s SET estado = 'retirada', "
            "resuelto_por = $2, resuelto_at = now(), resolucion_motivo = $3 "
            "WHERE s.id = $1 AND s.estado = 'pendiente' AND s.vence_at > now() "
            "AND s.solicitado_por = $2 "
            "AND EXISTS (SELECT 1 FROM radicados r WHERE r.id = s.radicado_id "
            "            AND r.nivel_seguridad <= $4) "
            f"RETURNING {_COLS}",
            solicitud_id,
            solicitante,
            motivo,
            max_clearance,
        )

    async def diagnostico(
        self, solicitud_id: uuid.UUID, max_clearance: int
    ) -> asyncpg.Record | None:
        """Estado de la solicitud para EXPLICAR por qué una decisión no aplicó.

        Lleva el MISMO gate de clearance: si el llamante no alcanza el radicado,
        devuelve `None` y el servicio responde el 404 neutro. Solo explica; la
        guarda es el `WHERE` del UPDATE.
        """
        return await self._conn.fetchrow(
            f"SELECT {_COLS}, r.nivel_seguridad AS nivel_actual, "
            "(s.estado = 'pendiente' AND s.vence_at <= now()) AS vencida "
            "FROM reclasificacion_solicitudes s JOIN radicados r ON r.id = s.radicado_id "
            "WHERE s.id = $1 AND r.nivel_seguridad <= $2",
            solicitud_id,
            max_clearance,
        )

    async def historial(
        self, radicado_id: uuid.UUID, max_clearance: int
    ) -> list[asyncpg.Record]:
        """Solicitudes de un radicado, la más reciente primero; clearance en el `WHERE`."""
        return list(
            await self._conn.fetch(
                f"SELECT {_COLS}, r.tracking_number FROM reclasificacion_solicitudes s "
                "JOIN radicados r ON r.id = s.radicado_id "
                "WHERE s.radicado_id = $1 AND r.nivel_seguridad <= $2 "
                "ORDER BY s.solicitado_at DESC",
                radicado_id,
                max_clearance,
            )
        )

    async def cola(
        self, *, max_clearance: int, actor: uuid.UUID | None, offset: int, limit: int
    ) -> tuple[list[asyncpg.Record], int]:
        """Cola de decisión: pendientes vigentes, FIFO por `solicitado_at`.

        Trae `tracking_number` y `subject` del radicado porque sin ellos la cola no
        dice de qué habla; el gate de clearance es lo que hace seguro traerlos. Filas
        y total salen de la MISMA condición (`_VISIBLE_LISTA`). `propia` marca las
        solicitudes del propio llamante, que no puede decidir.
        """
        filas = await self._conn.fetch(
            f"SELECT {_COLS}, r.tracking_number, r.subject, r.doc_type, r.nivel_seguridad, "
            "COALESCE(s.solicitado_por = $4, FALSE) AS propia "
            "FROM reclasificacion_solicitudes s JOIN radicados r ON r.id = s.radicado_id "
            f"WHERE {self._VISIBLE_LISTA} "
            "ORDER BY s.solicitado_at ASC OFFSET $2 LIMIT $3",
            max_clearance,
            offset,
            limit,
            actor,
        )
        total = await self._conn.fetchval(
            "SELECT COUNT(*) FROM reclasificacion_solicitudes s "
            "JOIN radicados r ON r.id = s.radicado_id "
            f"WHERE {self._VISIBLE_LISTA}",
            max_clearance,
        )
        return list(filas), int(total or 0)
