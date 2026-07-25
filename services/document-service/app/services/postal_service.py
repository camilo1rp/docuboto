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

import json
import logging
import uuid
from typing import Any

import asyncpg
from fastapi import HTTPException, status
from orpycamcp_common import audit, events
from pydantic import ValidationError

from app.core.postal_webhook_auth import verify_signature
from app.repositories.postal_repository import PostalRepository
from app.schemas.postal import (
    CallbackResult,
    PostalCallbackPayload,
    ShipmentCreate,
    ShipmentEvent,
    ShipmentResponse,
    ShipmentStatusUpdate,
    TrackingResponse,
)
from app.services.postal_provider import consultar_estado, generar_guia, mapear_estado

logger = logging.getLogger(__name__)

_SERVICE = "document-service"
_STREAM = "orpycamcp.document.events"

_VALID = {
    "registrado": {"en_transito", "fallido"},
    "en_transito": {"entregado", "devuelto", "fallido"},
    "entregado": set(),
    "devuelto": set(),
    "fallido": set(),
}

# Estados internos cuya llegada emite un evento de dominio para E16.
_STREAM_EVENT = {
    "en_transito": "document.envio.en_transito",
    "entregado": "document.envio.entregado",
    "devuelto": "document.envio.devuelto",
    "fallido": "document.envio.fallido",
}


class PostalService:
    def __init__(
        self,
        conn: asyncpg.Connection,
        redis: Any | None = None,
        tenant_slug: str | None = None,
    ) -> None:
        self._conn = conn
        self._repo = PostalRepository(conn)
        self._redis = redis
        self._slug = tenant_slug

    async def _response(self, row) -> ShipmentResponse:
        events = await self._repo.events(row["id"])
        return ShipmentResponse(
            **dict(row),
            eventos=[
                ShipmentEvent(estado=e["estado"], descripcion=e["descripcion"], ts=e["ts"])
                for e in events
            ],
        )

    async def create(
        self, radicado_id: uuid.UUID, payload: ShipmentCreate, clearance: int = 3
    ) -> ShipmentResponse:
        # clearance=3 (CLASIFICADA) = sin restricción; el router pasa el real.
        # No-read-up: un radicado sobre-clearance devuelve el MISMO 404
        # `radicado_not_found` que uno inexistente (indistinguibles, no-oráculo).
        guia = generar_guia(payload.operador, radicado_id)
        no_encontrado = HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "radicado_not_found", "detail": str(radicado_id), "status": 404},
        )
        try:
            row = await self._repo.insert(
                radicado_id=radicado_id,
                operador=payload.operador,
                guia=guia,
                destinatario=payload.destinatario,
                direccion=payload.direccion,
                max_clearance=clearance,
            )
        except asyncpg.ForeignKeyViolationError:
            raise no_encontrado
        except asyncpg.UniqueViolationError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "error": "envio_duplicado",
                    "detail": f"Guía {guia} ya existe",
                    "status": 409,
                },
            )
        if row is None:
            raise no_encontrado
        return await self._response(row)

    async def get(self, shipment_id: uuid.UUID, clearance: int = 3) -> ShipmentResponse:
        # clearance=3 (CLASIFICADA) = no restriction; router passes the real caller clearance.
        row = await self._repo.get(shipment_id, clearance)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "envio_not_found", "detail": str(shipment_id), "status": 404},
            )
        return await self._response(row)

    async def list_for_radicado(
        self, radicado_id: uuid.UUID, clearance: int = 3
    ) -> list[ShipmentResponse]:
        rows = await self._repo.list_by_radicado(radicado_id, clearance)
        return [await self._response(r) for r in rows]

    async def list_all(
        self, *, estado: str | None, page: int, size: int, clearance: int
    ) -> tuple[list[ShipmentResponse], int]:
        """Listado transversal paginado del tenant para la bandeja (E20)."""
        rows, total = await self._repo.list_all_paginated(
            estado=estado, page=page, size=size, clearance=clearance
        )
        return [await self._response(r) for r in rows], total

    async def update_estado(
        self, shipment_id: uuid.UUID, payload: ShipmentStatusUpdate, clearance: int = 3
    ) -> ShipmentResponse:
        # RF-SEG-08 (cierra el residual D-14): la transición manual usa el
        # clearance REAL del llamante — un envío cuyo radicado supera su clearance
        # colapsa a 404 (no se puede transicionar ni leer de vuelta lo oculto).
        current = await self._repo.get(shipment_id, clearance)
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

    async def tracking(self, shipment_id: uuid.UUID, clearance: int = 3) -> TrackingResponse:
        """Consulta PULL del estado por guía contra el operador (F5, solo lectura).

        Gate no-read-up por clearance (404 si supera). NO aplica la transición:
        sólo consulta y mapea; el callback entrante es quien muta el estado."""
        row = await self._repo.get(shipment_id, clearance)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "envio_not_found", "detail": str(shipment_id), "status": 404},
            )
        crudo = consultar_estado(row["operador"], row["guia"])
        mapeado = mapear_estado(row["operador"], crudo) if crudo is not None else None
        return TrackingResponse(
            shipment_id=row["id"],
            operador=row["operador"],
            guia=row["guia"],
            estado_interno_actual=row["estado"],
            estado_operador=crudo,
            estado_interno_mapeado=mapeado,
            operador_conectado=crudo is not None,
        )

    async def apply_operator_callback(
        self, raw_body: bytes, signature_header: str | None, tenant_slug: str
    ) -> CallbackResult:
        """Webhook ENTRANTE del operador (E20 F5): autentica por HMAC, deduplica y
        aplica la transición como acto de SISTEMA (nunca un usuario).

        Fail-closed en cada puerta: sin credencial del operador o firma inválida →
        401; estado del operador no mapeable → 422; guía desconocida → 404. La
        idempotencia (dedup por `(operador, event_id)`) + la transición + el
        asiento son atómicos; el evento Redis se emite tras confirmar la tx.
        """
        self._slug = tenant_slug
        # 1) Parseo estructural (para enrutar al secreto del operador). NO se
        #    confía en el cuerpo hasta verificar el HMAC sobre los bytes crudos.
        try:
            data = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail={"error": "cuerpo_invalido", "detail": "JSON inválido", "status": 400},
            )
        operador = data.get("operador") if isinstance(data, dict) else None
        secret = (
            await self._repo.get_operator_credential(operador)
            if isinstance(operador, str)
            else None
        )
        # 2) HMAC fail-closed. Credencial ausente y firma inválida devuelven el
        #    MISMO 401 (no se distingue "operador no configurado" de "firma mala").
        #    Se computa el HMAC SIEMPRE (contra un secreto centinela cuando no hay
        #    credencial) para no filtrar por temporización qué operadores están
        #    provisionados en el tenant (remediación de auditoría, Baja).
        probe_secret = secret if secret is not None else "\x00unconfigured"
        valid = verify_signature(probe_secret, raw_body, signature_header)
        if secret is None or not valid:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail={"error": "firma_invalida", "detail": "HMAC inválido", "status": 401},
            )
        # 3) Validación de esquema (ya autenticado).
        try:
            payload = PostalCallbackPayload.model_validate(data)
        except ValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={"error": "payload_invalido", "detail": exc.errors(), "status": 422},
            )
        # 4) Mapeo honesto del estado del operador (no se adivina).
        interno = mapear_estado(payload.operador, payload.estado_operador)
        if interno is None:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "estado_operador_no_mapeable",
                    "detail": f"'{payload.estado_operador}' no se mapea para {payload.operador}",
                    "status": 422,
                },
            )
        # 5) Localizar el envío por guía (acto de sistema, sin clearance).
        shipment = await self._repo.find_by_guia(payload.operador, payload.guia)
        if shipment is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "envio_no_encontrado",
                    "detail": f"Guía {payload.guia} desconocida",
                    "status": 404,
                },
            )

        actor = f"postal:{payload.operador}"
        current = shipment["estado"]
        emit_estado: str | None = None
        async with self._conn.transaction():
            # 6) COMPUERTA de idempotencia: si ya se procesó este event_id, no-op.
            delivery_id = await self._repo.try_insert_delivery(
                operador=payload.operador,
                event_id=payload.event_id,
                guia=payload.guia,
                estado_operador=payload.estado_operador,
                estado_interno=interno,
                shipment_id=shipment["id"],
            )
            if delivery_id is None:
                return CallbackResult(
                    status="duplicate", shipment_id=shipment["id"], estado=current
                )
            if interno in _VALID.get(current, set()):
                # Transición válida: se aplica y se audita. `motivo` sólo se
                # persiste cuando el estado resuelto es 'devuelto'.
                motivo = payload.motivo if interno == "devuelto" else None
                await self._repo.update_estado(
                    shipment["id"], interno, payload.descripcion, motivo
                )
                await self._repo.mark_delivery_applied(delivery_id)
                await audit.append(
                    self._conn,
                    tenant_slug=self._slug,
                    service=_SERVICE,
                    action="document.envio_estado_operador",
                    object_type="postal_shipment",
                    object_ref=payload.guia,
                    actor=actor,
                    payload={
                        "de": current,
                        "a": interno,
                        "operador": payload.operador,
                        "event_id": payload.event_id,
                    },
                )
                emit_estado = interno
                result = CallbackResult(
                    status="applied", shipment_id=shipment["id"], estado=interno
                )
            else:
                # Misma etiqueta o transición inválida (retroceso / desde terminal):
                # se REGISTRA la notificación sin cambiar el estado (honesto,
                # no rompe el invariante de la máquina de estados).
                nota = f"[{payload.operador}:{payload.estado_operador}] no aplicada"
                if payload.descripcion:
                    nota += f" — {payload.descripcion}"
                await self._repo.add_event(shipment["id"], current, nota)
                await audit.append(
                    self._conn,
                    tenant_slug=self._slug,
                    service=_SERVICE,
                    action="document.envio_estado_operador_no_aplicada",
                    object_type="postal_shipment",
                    object_ref=payload.guia,
                    actor=actor,
                    payload={
                        "estado_actual": current,
                        "estado_operador": payload.estado_operador,
                        "mapeado_a": interno,
                        "event_id": payload.event_id,
                    },
                )
                result = CallbackResult(
                    status="recorded_no_change", shipment_id=shipment["id"], estado=current
                )

        # 7) Evento de dominio para E16 — tras confirmar la tx, best-effort.
        if emit_estado is not None:
            await self._emit_estado(shipment, emit_estado, actor)
        return result

    async def _emit_estado(self, shipment: asyncpg.Record, estado: str, actor: str) -> None:
        if self._redis is None or estado not in _STREAM_EVENT:
            return
        try:
            await events.emit(
                self._redis,
                _STREAM,
                _STREAM_EVENT[estado],
                tenant_slug=self._slug or "",
                actor=actor,
                # dedup_key con tenant: el stream es global; evita cualquier
                # colisión cross-tenant (remediación de auditoría, Baja).
                dedup_key=f"{self._slug or ''}:{shipment['guia']}:{estado}",
                payload={
                    "shipment_id": str(shipment["id"]),
                    "radicado_id": str(shipment["radicado_id"]),
                    "operador": shipment["operador"],
                    "guia": shipment["guia"],
                    "estado": estado,
                },
            )
        except Exception:  # noqa: BLE001 — best-effort; la BD es la fuente de verdad
            logger.warning("emit envio estado (%s) failed", estado, exc_info=True)
