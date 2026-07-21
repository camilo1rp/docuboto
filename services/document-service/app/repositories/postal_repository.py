# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Repositorio de envíos postales (E20). search_path al tenant ya fijado por la conexión."""

from __future__ import annotations

import uuid

import asyncpg

_COLS = (
    "id, radicado_id, operador, guia, estado, destinatario, direccion, "
    "motivo_devolucion, created_at, updated_at"
)
# Prefixed for JOIN queries to avoid column-name ambiguity with the radicados table.
_COLS_S = (
    "s.id, s.radicado_id, s.operador, s.guia, s.estado, "
    "s.destinatario, s.direccion, s.motivo_devolucion, s.created_at, s.updated_at"
)


class PostalRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def insert(
        self,
        *,
        radicado_id: uuid.UUID,
        operador: str,
        guia: str,
        destinatario: str | None,
        direccion: str | None,
    ) -> asyncpg.Record:
        async with self._conn.transaction():
            row = await self._conn.fetchrow(
                f"""
                INSERT INTO postal_shipment
                    (radicado_id, operador, guia, destinatario, direccion)
                VALUES ($1, $2, $3, $4, $5)
                RETURNING {_COLS}
                """,
                radicado_id,
                operador,
                guia,
                destinatario,
                direccion,
            )
            assert row is not None
            await self._conn.execute(
                "INSERT INTO postal_shipment_event (shipment_id, estado, descripcion) "
                "VALUES ($1, 'registrado', 'Envío registrado')",
                row["id"],
            )
        return row

    async def get(self, shipment_id: uuid.UUID, clearance: int = 3) -> asyncpg.Record | None:
        # RF-SEG-08: join radicados to enforce no-read-up; clearance=3 (CLASIFICADA)
        # means no restriction and is used by internal callers (e.g. update_estado).
        return await self._conn.fetchrow(
            f"SELECT {_COLS_S} FROM postal_shipment s "
            "JOIN radicados r ON r.id = s.radicado_id "
            "WHERE s.id = $1 AND r.nivel_seguridad <= $2",
            shipment_id,
            clearance,
        )

    async def list_by_radicado(
        self, radicado_id: uuid.UUID, clearance: int = 3
    ) -> list[asyncpg.Record]:
        # RF-SEG-08: only return shipments whose parent radicado is within clearance.
        return list(
            await self._conn.fetch(
                f"SELECT {_COLS_S} FROM postal_shipment s "
                "JOIN radicados r ON r.id = s.radicado_id "
                "WHERE s.radicado_id = $1 AND r.nivel_seguridad <= $2 "
                "ORDER BY s.created_at",
                radicado_id,
                clearance,
            )
        )

    async def events(self, shipment_id: uuid.UUID) -> list[asyncpg.Record]:
        return list(
            await self._conn.fetch(
                "SELECT estado, descripcion, ts FROM postal_shipment_event "
                "WHERE shipment_id = $1 ORDER BY ts ASC",
                shipment_id,
            )
        )

    async def list_all_paginated(
        self, *, estado: str | None, page: int, size: int, clearance: int
    ) -> tuple[list[asyncpg.Record], int]:
        """Listado paginado transversal del tenant, filtrado por clearance y opcionalmente estado.

        RF-SEG-08: COUNT and SELECT use the same JOIN/filter so X-Total-Count never
        leaks the existence of shipments above the caller's clearance level.
        """
        offset = (page - 1) * size
        if estado is None:
            total: int = await self._conn.fetchval(
                "SELECT COUNT(*) FROM postal_shipment s "
                "JOIN radicados r ON r.id = s.radicado_id "
                "WHERE r.nivel_seguridad <= $1",
                clearance,
            )
            rows = list(
                await self._conn.fetch(
                    f"SELECT {_COLS_S} FROM postal_shipment s "
                    "JOIN radicados r ON r.id = s.radicado_id "
                    "WHERE r.nivel_seguridad <= $1 "
                    "ORDER BY s.created_at DESC LIMIT $2 OFFSET $3",
                    clearance,
                    size,
                    offset,
                )
            )
        else:
            total = await self._conn.fetchval(
                "SELECT COUNT(*) FROM postal_shipment s "
                "JOIN radicados r ON r.id = s.radicado_id "
                "WHERE r.nivel_seguridad <= $1 AND s.estado = $2",
                clearance,
                estado,
            )
            rows = list(
                await self._conn.fetch(
                    f"SELECT {_COLS_S} FROM postal_shipment s "
                    "JOIN radicados r ON r.id = s.radicado_id "
                    "WHERE r.nivel_seguridad <= $1 AND s.estado = $2 "
                    "ORDER BY s.created_at DESC LIMIT $3 OFFSET $4",
                    clearance,
                    estado,
                    size,
                    offset,
                )
            )
        return rows, total

    async def update_estado(
        self,
        shipment_id: uuid.UUID,
        estado: str,
        descripcion: str | None,
        motivo_devolucion: str | None = None,
    ) -> asyncpg.Record | None:
        async with self._conn.transaction():
            # `motivo_devolucion` sólo se fija cuando el llamante lo pasa (el
            # servicio lo hace únicamente para 'devuelto'); COALESCE conserva el
            # motivo previo si esta transición no lo trae.
            row = await self._conn.fetchrow(
                f"UPDATE postal_shipment SET estado = $2, updated_at = now(), "
                f"motivo_devolucion = COALESCE($3, motivo_devolucion) "
                f"WHERE id = $1 RETURNING {_COLS}",
                shipment_id,
                estado,
                motivo_devolucion,
            )
            if row is None:
                return None
            await self._conn.execute(
                "INSERT INTO postal_shipment_event (shipment_id, estado, descripcion) "
                "VALUES ($1, $2, $3)",
                shipment_id,
                estado,
                descripcion,
            )
        return row

    # ------------------------------------------------------------------
    # E20 F5 — callback entrante del operador
    # ------------------------------------------------------------------
    async def get_operator_credential(self, operador: str) -> str | None:
        """Secreto HMAC del operador en ESTE tenant; None si no hay credencial."""
        return await self._conn.fetchval(
            "SELECT webhook_secret FROM postal_operator_credential WHERE operador = $1",
            operador,
        )

    async def find_by_guia(self, operador: str, guia: str) -> asyncpg.Record | None:
        """Localiza el envío por (operador, guia). Sin filtro de clearance: el
        callback es un acto de SISTEMA (autenticado por HMAC), no una lectura
        humana — actualiza el estado de custodia aunque el radicado sea
        clasificado (completitud de la traza ≠ control de acceso de lectura)."""
        return await self._conn.fetchrow(
            f"SELECT {_COLS} FROM postal_shipment WHERE operador = $1 AND guia = $2",
            operador,
            guia,
        )

    async def try_insert_delivery(
        self,
        *,
        operador: str,
        event_id: str,
        guia: str,
        estado_operador: str,
        estado_interno: str | None,
        shipment_id: uuid.UUID,
    ) -> uuid.UUID | None:
        """Inserta la fila de dedup; None si (operador, event_id) ya existe.

        Es la COMPUERTA de idempotencia: ON CONFLICT DO NOTHING serializa dos
        callbacks concurrentes del mismo evento (sólo uno obtiene el id)."""
        return await self._conn.fetchval(
            "INSERT INTO postal_webhook_delivery "
            "(operador, event_id, guia, estado_operador, estado_interno, shipment_id) "
            "VALUES ($1, $2, $3, $4, $5, $6) "
            "ON CONFLICT (operador, event_id) DO NOTHING RETURNING id",
            operador,
            event_id,
            guia,
            estado_operador,
            estado_interno,
            shipment_id,
        )

    async def mark_delivery_applied(self, delivery_id: uuid.UUID) -> None:
        await self._conn.execute(
            "UPDATE postal_webhook_delivery SET aplicada = TRUE WHERE id = $1",
            delivery_id,
        )

    async def add_event(
        self, shipment_id: uuid.UUID, estado: str, descripcion: str | None
    ) -> None:
        """Registra un evento append-only SIN cambiar el estado del envío (usado
        cuando la notificación del operador no implica una transición válida)."""
        await self._conn.execute(
            "INSERT INTO postal_shipment_event (shipment_id, estado, descripcion) "
            "VALUES ($1, $2, $3)",
            shipment_id,
            estado,
            descripcion,
        )
