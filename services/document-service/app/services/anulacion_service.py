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

Al APROBAR se publica `document.radicado.annulled` (best-effort, gemelo de
`DocumentService._emit_created`) — knowledge-service (E21) lo consume para purgar
PERMANENTEMENTE el chunk indexado del radicado anulado (residual
"purga-en-anulación": un radicado ANULADO no debe seguir siendo consultable en el
índice semántico). Solicitar/rechazar NO emiten evento: solo aprobar cambia el
estado del radicado.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import asyncpg
from fastapi import HTTPException, status
from orpycamcp_common import events

from app.repositories.anulacion_repository import AnulacionRepository
from app.schemas.anulacion import (
    AnulacionDecision,
    AnulacionResponse,
    AnulacionSolicitud,
)

logger = logging.getLogger(__name__)

_STREAM = "orpycamcp.document.events"


def _resp(row) -> AnulacionResponse:
    return AnulacionResponse(**dict(row))


class AnulacionService:
    def __init__(self, conn: asyncpg.Connection, redis: Any | None = None) -> None:
        self._repo = AnulacionRepository(conn)
        self._redis = redis

    async def solicitar(
        self,
        radicado_id: uuid.UUID,
        payload: AnulacionSolicitud,
        solicitado_por: uuid.UUID | None,
        max_clearance: int,
    ) -> AnulacionResponse:
        estado_rad = await self._repo.radicado_status(radicado_id, max_clearance)
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
        max_clearance: int,
        tenant_slug: str | None = None,
    ) -> AnulacionResponse:
        active = await self._repo.get_active(radicado_id, max_clearance)
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
        # `repo.decide` ya commiteó (UPDATE radicado_anulacion + UPDATE radicados
        # en una sola transacción). El evento es best-effort y SOLO se publica al
        # aprobar — solicitar/rechazar no cambian el estado del radicado.
        if aprobar:
            tracking_number = await self._repo.get_tracking_number(radicado_id)
            await self._emit_annulled(radicado_id, tracking_number, tenant_slug, decidido_por)
        return _resp(row)

    async def _emit_annulled(
        self,
        radicado_id: uuid.UUID,
        tracking_number: str | None,
        tenant_slug: str | None,
        actor_id: uuid.UUID | None,
    ) -> None:
        if self._redis is None or not tracking_number or not tenant_slug:
            return
        try:
            await events.emit(
                self._redis,
                _STREAM,
                "document.radicado.annulled",
                tenant_slug=tenant_slug,
                actor=str(actor_id) if actor_id else None,
                dedup_key=f"{tracking_number}:annulled",
                payload={"id": str(radicado_id), "tracking_number": tracking_number},
            )
        except Exception:  # noqa: BLE001 — evento best-effort: nunca revierte la anulación.
            logger.warning(
                "No se pudo publicar el evento de anulación %s",
                tracking_number,
                exc_info=True,
            )

    async def aprobar(
        self, radicado_id, payload, decidido_por, max_clearance: int, tenant_slug: str | None = None
    ) -> AnulacionResponse:
        return await self._decidir(
            radicado_id,
            aprobar=True,
            payload=payload,
            decidido_por=decidido_por,
            max_clearance=max_clearance,
            tenant_slug=tenant_slug,
        )

    async def rechazar(
        self, radicado_id, payload, decidido_por, max_clearance: int
    ) -> AnulacionResponse:
        return await self._decidir(
            radicado_id,
            aprobar=False,
            payload=payload,
            decidido_por=decidido_por,
            max_clearance=max_clearance,
        )

    async def get(self, radicado_id: uuid.UUID, max_clearance: int) -> AnulacionResponse:
        row = await self._repo.get_latest(radicado_id, max_clearance)
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
