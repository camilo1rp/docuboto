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
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg
from fastapi import HTTPException, status
from orpycamcp_common import audit, events
from pydantic import ValidationError

from app.clients import storage_client
from app.core.config import settings
from app.core.postal_webhook_auth import (
    cifrar_secreto,
    descifrar_secreto,
    verify_any,
)
from app.repositories.postal_repository import PostalRepository
from app.schemas.postal import (
    CallbackResult,
    CredencialRotacion,
    CredencialRotada,
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
        self,
        radicado_id: uuid.UUID,
        payload: ShipmentCreate,
        clearance: int = 3,
        actor: uuid.UUID | None = None,
    ) -> ShipmentResponse:
        # clearance=3 (CLASIFICADA) = sin restricción; el router pasa el real.
        # No-read-up: un radicado sobre-clearance devuelve el MISMO 404
        # `radicado_not_found` que uno inexistente (indistinguibles, no-oráculo).
        guia = generar_guia(payload.operador, uuid.uuid4())
        no_encontrado = HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "radicado_not_found", "detail": str(radicado_id), "status": 404},
        )
        try:
            # Envío, evento inicial y asiento son un solo acto (ADR-010).
            async with self._conn.transaction():
                row = await self._repo.insert(
                    radicado_id=radicado_id,
                    operador=payload.operador,
                    guia=guia,
                    destinatario=payload.destinatario,
                    direccion=payload.direccion,
                    max_clearance=clearance,
                )
                if row is not None:
                    await audit.append(
                        self._conn,
                        tenant_slug=self._slug or "",
                        service=_SERVICE,
                        action="document.envio_registrado",
                        object_type="postal_shipment",
                        object_ref=guia,
                        actor=str(actor) if actor else None,
                        payload={
                            "shipment_id": str(row["id"]),
                            "radicado_id": str(radicado_id),
                            "operador": payload.operador,
                        },
                        canal=audit.CANAL_PETICION,
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
        self,
        shipment_id: uuid.UUID,
        payload: ShipmentStatusUpdate,
        clearance: int = 3,
        actor: uuid.UUID | None = None,
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
        # AREA-09-10: la constancia de entrega la resuelve el SERVIDOR. Un par
        # (file_id, sha256) que solo viene en el cuerpo no prueba nada: se cotejan
        # contra storage (que calculó la huella al subir, y solo conoce los ficheros
        # de ESTE tenant) y se asienta la huella de storage, no la del cliente.
        acuse_sha256: str | None = payload.acuse_sha256.lower() if payload.acuse_sha256 else None
        if payload.acuse_file_id is not None:
            acuse_sha256 = await self._verificar_acuse(payload.acuse_file_id, acuse_sha256)
        # Transición, evento y asiento atómicos. El UPDATE compara el estado
        # previo y el clearance en su `WHERE`: si entre la lectura de arriba y
        # aquí cambió el estado (p. ej. el callback del operador dejó `devuelto`)
        # o el radicado se reclasificó, no devuelve fila y se responde 409 en
        # vez de pisar un estado terminal.
        async with self._conn.transaction():
            row = await self._repo.update_estado(
                shipment_id,
                payload.estado,
                payload.descripcion,
                motivo_devolucion=payload.motivo,
                estado_previo=current["estado"],
                max_clearance=clearance,
                acuse_file_id=payload.acuse_file_id,
                acuse_sha256=acuse_sha256,
            )
            if row is None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={
                        "error": "transicion_invalida",
                        "detail": "El envío cambió de estado mientras se registraba la transición",
                        "status": 409,
                    },
                )
            await audit.append(
                self._conn,
                tenant_slug=self._slug or "",
                service=_SERVICE,
                action="document.envio_estado",
                object_type="postal_shipment",
                object_ref=current["guia"],
                actor=str(actor) if actor else None,
                payload={
                    "shipment_id": str(shipment_id),
                    "de": current["estado"],
                    "a": payload.estado,
                    "motivo": payload.motivo,
                    # La constancia de entrega queda en el asiento (AREA-09-10).
                    "acuse_file_id": str(payload.acuse_file_id) if payload.acuse_file_id else None,
                    "acuse_sha256": acuse_sha256,
                },
                canal=audit.CANAL_PETICION,
            )
        return await self._response(row)

    async def _verificar_acuse(self, file_id: uuid.UUID, declarada: str | None) -> str:
        """Devuelve la huella que storage custodia para `file_id`, o corta con 422/503.

        422 `acuse_invalido` si el fichero no existe en el tenant o la huella declarada
        no coincide; 503 si storage no responde (no se asienta una prueba sin cotejar).
        """
        try:
            real = await storage_client.fetch_file_sha256(
                tenant_slug=self._slug or "", file_id=file_id
            )
        except storage_client.StorageContentError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "acuse_no_verificable",
                    "detail": f"No se pudo verificar el acuse contra el almacén. ({exc})",
                    "status": 503,
                },
            ) from exc
        if not real or (declarada is not None and real.lower() != declarada):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "acuse_invalido",
                    "detail": "El acuse no existe en el almacén o su huella no coincide.",
                    "status": 422,
                },
            )
        return real.lower()

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

    def _secretos_vigentes(
        self,
        fila: Any,
        operador: str,
        tenant_slug: str,
        ahora: datetime | None = None,
    ) -> list[str]:
        """Secretos con los que hoy vale una firma de este operador (AREA-09-10).

        La ranura VIGENTE siempre; la ANTERIOR solo mientras `anterior_caduca_at` no
        haya pasado. La caducidad se decide AQUÍ, en Python, con el reloj del servicio:
        una ranura caducada no es un secreto «menos válido», es un secreto que ya no
        existe, y no debe llegar siquiera a `verify_any`.

        Un ciphertext que no descifra (clave maestra cambiada, fila copiada de otra
        entidad) se OMITE y se registra sin el valor: el callback cae en 401 en vez de
        aceptarse con un secreto que no se pudo probar.
        """
        ahora = ahora or datetime.now(UTC)
        clave = settings.email_encryption_key
        secretos: list[str] = []

        def _abrir(cifrado: Any, nonce: Any, ranura: str) -> None:
            try:
                secretos.append(
                    descifrar_secreto(
                        cifrado,
                        nonce=nonce,
                        clave_b64=clave,
                        tenant_slug=tenant_slug,
                        operador=operador,
                    )
                )
            except Exception:  # noqa: BLE001 — InvalidTag, clave ausente...: ranura inservible
                logger.error(
                    "postal: la ranura %s del operador %s no se pudo descifrar", ranura, operador
                )

        if fila["secreto_cifrado"] is not None:
            _abrir(fila["secreto_cifrado"], fila["secreto_nonce"], "vigente")
        elif fila["webhook_secret"]:
            secretos.append(fila["webhook_secret"])  # heredado, en claro
        caduca = fila["anterior_caduca_at"]
        if fila["anterior_cifrado"] is not None and caduca is not None and caduca > ahora:
            _abrir(fila["anterior_cifrado"], fila["anterior_nonce"], "anterior")
        return secretos

    async def rotar_credencial(
        self, operador: str, payload: CredencialRotacion, actor: uuid.UUID | None
    ) -> CredencialRotada:
        """Fija el secreto HMAC nuevo del operador; el anterior sigue valiendo `gracia_horas`.

        Todo o nada en UNA transacción con su asiento (`audit.append` fail-closed: si no
        se puede dejar traza, el secreto no cambia). El secreto NUNCA aparece en la
        respuesta ni en el asiento. Sin clave maestra configurada se responde 503: es
        preferible no rotar a guardar el secreto en claro.
        """
        if not settings.email_encryption_key:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "custodia_no_configurada",
                    "detail": "No hay clave maestra de custodia (EMAIL_ENCRYPTION_KEY): "
                    "no se guardan secretos en claro.",
                    "status": 503,
                },
            )
        slug = self._slug or ""
        clave = settings.email_encryption_key
        ahora = datetime.now(UTC)
        async with self._conn.transaction():
            previa = await self._repo.lock_operator_credential(operador)
            anterior: tuple[bytes, bytes] | None = None
            caduca: datetime | None = None
            if previa is not None and payload.gracia_horas > 0:
                if previa["secreto_cifrado"] is not None:
                    # Misma AAD (tenant+operador): el ciphertext se mueve de ranura sin
                    # re-cifrarse.
                    anterior = (bytes(previa["secreto_cifrado"]), bytes(previa["secreto_nonce"]))
                elif previa["webhook_secret"]:
                    anterior = cifrar_secreto(
                        previa["webhook_secret"], clave_b64=clave, tenant_slug=slug, operador=operador
                    )
                if anterior is not None:
                    caduca = ahora + timedelta(hours=payload.gracia_horas)
            cifrado, nonce = cifrar_secreto(
                payload.secreto, clave_b64=clave, tenant_slug=slug, operador=operador
            )
            rotada_at = await self._repo.upsert_operator_credential(
                operador,
                cifrado=cifrado,
                nonce=nonce,
                anterior_cifrado=anterior[0] if anterior else None,
                anterior_nonce=anterior[1] if anterior else None,
                anterior_caduca_at=caduca,
                rotado_por=actor,
            )
            await audit.append(
                self._conn,
                tenant_slug=slug,
                service=_SERVICE,
                action="document.postal_credencial_rotada",
                object_type="postal_operator_credential",
                object_ref=operador,
                actor=str(actor) if actor else None,
                payload={
                    "operador": operador,
                    "gracia_horas": payload.gracia_horas,
                    "con_anterior_vigente": anterior is not None,
                    "primera_vez": previa is None,
                },
                canal=audit.CANAL_PETICION,
            )
        return CredencialRotada(
            operador=operador, rotada_at=rotada_at, anterior_vigente_hasta=caduca
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
        fila = (
            await self._repo.get_operator_credential(operador)
            if isinstance(operador, str)
            else None
        )
        secretos = self._secretos_vigentes(fila, str(operador), tenant_slug) if fila else []
        # 2) HMAC fail-closed. Credencial ausente y firma inválida devuelven el
        #    MISMO 401 (no se distingue "operador no configurado" de "firma mala").
        #    Se computa el HMAC SIEMPRE (contra un secreto centinela cuando no hay
        #    credencial) para no filtrar por temporización qué operadores están
        #    provisionados en el tenant (remediación de auditoría, Baja). Con dos
        #    ranuras (rotación, AREA-09-10) se prueban las vigentes sin cortocircuito.
        valid = verify_any(secretos or ["\x00unconfigured"], raw_body, signature_header)
        if not secretos or not valid:
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
            aplicado = None
            if interno in _VALID.get(current, set()):
                # Transición válida: se aplica y se audita. `motivo` sólo se
                # persiste cuando el estado resuelto es 'devuelto'. El UPDATE
                # compara el estado previo: si una transición manual se coló
                # entre la lectura y aquí, no devuelve fila y se cae a la rama
                # «no aplicada» con el estado REAL, sin pisarlo.
                motivo = payload.motivo if interno == "devuelto" else None
                aplicado = await self._repo.update_estado(
                    shipment["id"],
                    interno,
                    payload.descripcion,
                    motivo,
                    estado_previo=current,
                    max_clearance=None,  # acto de sistema autenticado por HMAC
                )
                if aplicado is None:
                    fresco = await self._repo.find_by_guia(payload.operador, payload.guia)
                    if fresco is not None:
                        current = fresco["estado"]
            if aplicado is not None:
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
                    canal=audit.CANAL_PETICION,
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
                    canal=audit.CANAL_PETICION,
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
