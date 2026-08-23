# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Repositorio de anulación de radicados (2 pasos). search_path fijado por la conexión."""

from __future__ import annotations

import uuid

import asyncpg

_COLS = (
    "id, radicado_id, estado, causal, motivo, solicitado_por, solicitado_at, "
    "decidido_por, decidido_at, observacion"
)


class AnulacionRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    # El no-read-up (RF-SEG-08) se aplica en las TRES consultas de entrada
    # (`get_active`, `get_latest`, `radicado_status`), que son las únicas puertas
    # por las que el servicio de anulación alcanza un radicado. La causal y el
    # motivo de una anulación describen el documento, así que exponerlos sin gate
    # filtraría material clasificado; y aprobar la anulación de un radicado que no
    # se puede ver sería una mutación del ciclo de vida sin autoridad (art. 28).
    _VISIBLE = "EXISTS (SELECT 1 FROM radicados r WHERE r.id = $1 AND r.nivel_seguridad <= $2)"

    #: Condición de la COLA: solicitudes sin decidir, recortadas por clearance.
    #: Una sola definición para las filas y para el total — si divergieran, la
    #: diferencia entre lo contado y lo mostrado sería la existencia de las
    #: solicitudes sobre radicados clasificados.
    _VISIBLE_LISTA = "a.estado = 'solicitada' AND r.nivel_seguridad <= $1"


    async def list_pendientes(
        self, *, max_clearance: int, offset: int, limit: int
    ) -> tuple[list[asyncpg.Record], int]:
        """Cola de solicitudes SIN DECIDIR, con el radicado al que se refieren.

        Hasta ahora no existía: la pantalla del aprobador solo podía consultar
        el estado *de un radicado concreto*, así que había que saber de antemano
        cuál se quería revisar. Una cola de trabajo que obliga a adivinar su
        propio contenido no es una cola.

        **No-read-up (RF-SEG-08) en la MISMA condición para las filas y para el
        total.** Es el punto delicado: filtrar solo las filas y contar sin
        filtrar diría «hay 7 pendientes» mostrando 5, y esa diferencia es
        precisamente la existencia de dos solicitudes sobre radicados
        clasificados. La condición vive UNA vez (`_VISIBLE_LISTA`) y las dos
        consultas la comparten para que no puedan divergir.

        Orden FIFO por `solicitado_at`: una anulación pendiente bloquea el ciclo
        de vida del documento (Ley 594/2000 art. 28), así que la más antigua es
        la más urgente — no la más reciente.

        Se traen `tracking_number`, `subject` y `doc_type` del radicado porque
        sin ellos la cola no dice de qué habla; el gate de clearance de arriba
        es justo lo que hace seguro traerlos.
        """
        filas = await self._conn.fetch(
            f"""
            SELECT a.id, a.radicado_id, a.estado, a.causal, a.motivo,
                   a.solicitado_por, a.solicitado_at,
                   r.tracking_number, r.subject, r.doc_type, r.status AS radicado_status
            FROM radicado_anulacion a
            JOIN radicados r ON r.id = a.radicado_id
            WHERE {self._VISIBLE_LISTA}
            ORDER BY a.solicitado_at ASC
            OFFSET $2 LIMIT $3
            """,
            max_clearance,
            offset,
            limit,
        )
        total = await self._conn.fetchval(
            f"""
            SELECT COUNT(*)
            FROM radicado_anulacion a
            JOIN radicados r ON r.id = a.radicado_id
            WHERE {self._VISIBLE_LISTA}
            """,
            max_clearance,
        )
        return list(filas), int(total or 0)

    async def get_active(self, radicado_id: uuid.UUID, max_clearance: int) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            f"SELECT {_COLS} FROM radicado_anulacion "
            f"WHERE radicado_id = $1 AND estado = 'solicitada' AND {self._VISIBLE}",
            radicado_id,
            max_clearance,
        )

    async def get_latest(self, radicado_id: uuid.UUID, max_clearance: int) -> asyncpg.Record | None:
        return await self._conn.fetchrow(
            f"SELECT {_COLS} FROM radicado_anulacion WHERE radicado_id = $1 "
            f"AND {self._VISIBLE} ORDER BY solicitado_at DESC LIMIT 1",
            radicado_id,
            max_clearance,
        )

    async def radicado_status(self, radicado_id: uuid.UUID, max_clearance: int) -> str | None:
        return await self._conn.fetchval(
            "SELECT status FROM radicados WHERE id = $1 AND nivel_seguridad <= $2",
            radicado_id,
            max_clearance,
        )

    async def radicado_estado_y_tipo(
        self, radicado_id: uuid.UUID, max_clearance: int
    ) -> tuple[str, str] | None:
        """Estado Y tipo en una sola lectura, para decidir si el acto aplica.

        El tipo hace falta porque una Entrada NO se anula (ver
        `app/core/reglas_tipo.py`) y esa comprobación es previa a cualquier
        consideración de permiso. Se lee junto al estado y no en una consulta
        aparte: son dos datos de la misma fila y el no-read-up
        (`nivel_seguridad <= $2`) tiene que aplicarse igual a los dos — con dos
        lecturas separadas sería una repetición fácil de olvidar en una de ellas.
        """
        fila = await self._conn.fetchrow(
            "SELECT status, doc_type FROM radicados WHERE id = $1 AND nivel_seguridad <= $2",
            radicado_id,
            max_clearance,
        )
        return (fila["status"], fila["doc_type"]) if fila is not None else None

    async def get_tracking_number(self, radicado_id: uuid.UUID) -> str | None:
        """Usado tras aprobar la anulación para publicar `document.radicado.annulled`
        (el sobre del evento, gemelo de `_emit_created`, necesita el tracking_number
        del radicado — `radicado_anulacion` no lo guarda). Consulta separada
        deliberada: no ensancha el contrato de `decide()` (que ya está en producción)."""
        return await self._conn.fetchval(
            "SELECT tracking_number FROM radicados WHERE id = $1", radicado_id
        )

    async def insert_solicitud(
        self,
        *,
        radicado_id: uuid.UUID,
        causal: str,
        motivo: str | None,
        solicitado_por: uuid.UUID | None,
    ) -> asyncpg.Record:
        row = await self._conn.fetchrow(
            f"INSERT INTO radicado_anulacion (radicado_id, causal, motivo, solicitado_por) "
            f"VALUES ($1, $2, $3, $4) RETURNING {_COLS}",
            radicado_id,
            causal,
            motivo,
            solicitado_por,
        )
        assert row is not None
        return row

    async def decide(
        self,
        *,
        anulacion_id: uuid.UUID,
        radicado_id: uuid.UUID,
        aprobar: bool,
        decidido_por: uuid.UUID | None,
        observacion: str | None,
    ) -> asyncpg.Record:
        """Aprueba/rechaza atómicamente; al aprobar marca el radicado como anulado."""
        estado = "aprobada" if aprobar else "rechazada"
        async with self._conn.transaction():
            row = await self._conn.fetchrow(
                f"UPDATE radicado_anulacion SET estado=$2, decidido_por=$3, "
                f"decidido_at=now(), observacion=$4 WHERE id=$1 RETURNING {_COLS}",
                anulacion_id,
                estado,
                decidido_por,
                observacion,
            )
            if aprobar:
                await self._conn.execute(
                    "UPDATE radicados SET status='anulado' WHERE id=$1", radicado_id
                )
        assert row is not None
        return row
