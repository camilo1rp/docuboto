# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Anulación de radicado en dos pasos (paridad legado).

Solicitar → (aprobar | rechazar). La ley prohíbe borrar: al aprobar, el radicado
pasa a `anulado` (el número se conserva). A lo sumo una solicitud activa por radicado.
"""

from __future__ import annotations

import uuid

import asyncpg
from fastapi import HTTPException, status

from app.repositories.anulacion_repository import AnulacionRepository
from app.schemas.anulacion import (
    AnulacionDecision,
    AnulacionResponse,
    AnulacionSolicitud,
)


def _resp(row) -> AnulacionResponse:
    return AnulacionResponse(**dict(row))


class AnulacionService:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._repo = AnulacionRepository(conn)

    async def solicitar(
        self,
        radicado_id: uuid.UUID,
        payload: AnulacionSolicitud,
        solicitado_por: uuid.UUID | None,
    ) -> AnulacionResponse:
        estado_rad = await self._repo.radicado_status(radicado_id)
        if estado_rad is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "document_not_found", "detail": str(radicado_id), "status": 404},
            )
        if estado_rad == "anulado":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "ya_anulado",
                    "detail": "El radicado ya está anulado",
                    "status": 409,
                },
            )
        try:
            row = await self._repo.insert_solicitud(
                radicado_id=radicado_id,
                causal=payload.causal,
                motivo=payload.motivo,
                solicitado_por=solicitado_por,
            )
        except asyncpg.UniqueViolationError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "solicitud_activa",
                    "detail": "Ya hay una solicitud de anulación pendiente",
                    "status": 409,
                },
            )
        return _resp(row)

    async def _decidir(
        self,
        radicado_id: uuid.UUID,
        *,
        aprobar: bool,
        payload: AnulacionDecision,
        decidido_por: uuid.UUID | None,
    ) -> AnulacionResponse:
        active = await self._repo.get_active(radicado_id)
        if active is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "sin_solicitud_activa",
                    "detail": "No hay solicitud de anulación pendiente",
                    "status": 404,
                },
            )
        row = await self._repo.decide(
            anulacion_id=active["id"],
            radicado_id=radicado_id,
            aprobar=aprobar,
            decidido_por=decidido_por,
            observacion=payload.observacion,
        )
        return _resp(row)

    async def aprobar(self, radicado_id, payload, decidido_por) -> AnulacionResponse:
        return await self._decidir(
            radicado_id, aprobar=True, payload=payload, decidido_por=decidido_por
        )

    async def rechazar(self, radicado_id, payload, decidido_por) -> AnulacionResponse:
        return await self._decidir(
            radicado_id, aprobar=False, payload=payload, decidido_por=decidido_por
        )

    async def get(self, radicado_id: uuid.UUID) -> AnulacionResponse:
        row = await self._repo.get_latest(radicado_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "sin_anulacion",
                    "detail": "El radicado no tiene anulaciones",
                    "status": 404,
                },
            )
        return _resp(row)
