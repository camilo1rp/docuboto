# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Envíos postales de radicados (E20).

Despacha un radicado con un operador postal (guía vía proveedor pluggable) y
gestiona el estado de entrega con una máquina de estados, conservando el historial.
"""

from __future__ import annotations

import uuid

import asyncpg
from fastapi import HTTPException, status

from app.repositories.postal_repository import PostalRepository
from app.schemas.postal import (
    ShipmentCreate,
    ShipmentEvent,
    ShipmentResponse,
    ShipmentStatusUpdate,
)
from app.services.postal_provider import generar_guia

_VALID = {
    "registrado": {"en_transito", "fallido"},
    "en_transito": {"entregado", "devuelto", "fallido"},
    "entregado": set(),
    "devuelto": set(),
    "fallido": set(),
}


class PostalService:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._repo = PostalRepository(conn)

    async def _response(self, row) -> ShipmentResponse:
        events = await self._repo.events(row["id"])
        return ShipmentResponse(
            **dict(row),
            eventos=[
                ShipmentEvent(estado=e["estado"], descripcion=e["descripcion"], ts=e["ts"])
                for e in events
            ],
        )

    async def create(self, radicado_id: uuid.UUID, payload: ShipmentCreate) -> ShipmentResponse:
        guia = generar_guia(payload.operador, radicado_id)
        try:
            row = await self._repo.insert(
                radicado_id=radicado_id,
                operador=payload.operador,
                guia=guia,
                destinatario=payload.destinatario,
                direccion=payload.direccion,
            )
        except asyncpg.ForeignKeyViolationError:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "radicado_not_found", "detail": str(radicado_id), "status": 404},
            )
        except asyncpg.UniqueViolationError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "envio_duplicado",
                    "detail": f"Guía {guia} ya existe",
                    "status": 409,
                },
            )
        return await self._response(row)

    async def get(self, shipment_id: uuid.UUID) -> ShipmentResponse:
        row = await self._repo.get(shipment_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "envio_not_found", "detail": str(shipment_id), "status": 404},
            )
        return await self._response(row)

    async def list_for_radicado(self, radicado_id: uuid.UUID) -> list[ShipmentResponse]:
        rows = await self._repo.list_by_radicado(radicado_id)
        return [await self._response(r) for r in rows]

    async def update_estado(
        self, shipment_id: uuid.UUID, payload: ShipmentStatusUpdate
    ) -> ShipmentResponse:
        current = await self._repo.get(shipment_id)
        if current is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "envio_not_found", "detail": str(shipment_id), "status": 404},
            )
        if payload.estado not in _VALID.get(current["estado"], set()):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "transicion_invalida",
                    "detail": f"No se puede pasar de '{current['estado']}' a '{payload.estado}'",
                    "status": 409,
                },
            )
        row = await self._repo.update_estado(shipment_id, payload.estado, payload.descripcion)
        return await self._response(row)
