# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas de envíos postales (E20)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

_OPERADOR = Literal["4-72", "servientrega", "otro"]
_ESTADO = Literal["registrado", "en_transito", "entregado", "devuelto", "fallido"]


class ShipmentCreate(BaseModel):
    operador: _OPERADOR
    destinatario: str | None = Field(default=None, max_length=255)
    direccion: str | None = Field(default=None, max_length=500)


class ShipmentStatusUpdate(BaseModel):
    estado: _ESTADO
    descripcion: str | None = Field(default=None, max_length=500)
    # La causal, en su campo. El webhook del operador ya alimentaba
    # `motivo_devolucion` (ver `PostalCallbackPayload`), pero la devolución que
    # registra una PERSONA desde la pantalla no tenía por dónde: la causal
    # acababa embebida en el texto libre de `descripcion` y el campo quedaba
    # NULL. El mismo hecho, registrado por dos caminos, con distinta calidad de
    # dato — y el manual, que es el habitual, era el peor: sin causal
    # consultable no hay forma de reportar por qué se devuelven los envíos.
    motivo: str | None = Field(default=None, max_length=200)
    #: Acuse de recibo (AREA-09-10): el `file_id` del soporte subido a storage y su
    #: SHA-256. Obligatorios para pasar A MANO a `entregado`: sin la constancia de
    #: entrega, «entregado» es la palabra de quien lo marca. El callback firmado del
    #: operador no los necesita: su firma es su prueba.
    acuse_file_id: uuid.UUID | None = None
    acuse_sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")

    @model_validator(mode="after")
    def _entregado_exige_acuse(self) -> ShipmentStatusUpdate:
        if self.estado == "entregado" and (self.acuse_file_id is None or not self.acuse_sha256):
            raise ValueError(
                "Pasar un envío a 'entregado' exige el acuse de recibo "
                "(acuse_file_id y acuse_sha256)"
            )
        return self


class CredencialRotacion(BaseModel):
    """Nuevo secreto HMAC del operador (AREA-09-10). Nunca se devuelve."""

    secreto: str = Field(..., min_length=32, max_length=200)
    #: Horas durante las que el secreto ANTERIOR sigue valiendo, para que el operador
    #: tenga tiempo de cambiar el suyo sin que sus callbacks caigan en 401. 0 = corte.
    gracia_horas: int = Field(default=24, ge=0, le=168)


class CredencialRotada(BaseModel):
    operador: str
    rotada_at: datetime
    #: Hasta cuándo vale el secreto anterior; `None` si no hay ranura anterior vigente.
    anterior_vigente_hasta: datetime | None = None


class ShipmentEvent(BaseModel):
    estado: str
    descripcion: str | None
    #: Cuándo el sistema RECIBIÓ el aviso del operador (o el cambio manual).
    ts: datetime
    #: Fecha del hecho postal según el operador (campo firmado del callback); None si no la mandó.
    ocurrido_operador_at: datetime | None = None


class ShipmentResponse(BaseModel):
    id: uuid.UUID
    radicado_id: uuid.UUID
    # El número de radicado, que es como se nombra un documento en la entidad.
    # Opcional por compatibilidad: una fila servida por un camino que aún no lo
    # traiga responde `null` en vez de romper la respuesta entera.
    tracking_number: str | None = None
    operador: str
    guia: str
    estado: str
    destinatario: str | None
    direccion: str | None
    motivo_devolucion: str | None = None
    acuse_file_id: uuid.UUID | None = None
    acuse_sha256: str | None = None
    created_at: datetime
    updated_at: datetime
    eventos: list[ShipmentEvent] = []


class PostalCallbackPayload(BaseModel):
    """Cuerpo del webhook ENTRANTE del operador postal (E20 F5).

    `operador` selecciona el secreto HMAC y el mapa de estados; `event_id` es la
    clave de idempotencia (dedup por `(operador, event_id)`); `estado_operador`
    es la etiqueta cruda del operador (se mapea a la máquina interna); `motivo`
    alimenta `motivo_devolucion` cuando el estado resuelve a `devuelto`.
    """

    operador: _OPERADOR
    guia: str = Field(max_length=60)
    estado_operador: str = Field(max_length=40)
    event_id: str = Field(min_length=1, max_length=120)
    descripcion: str | None = Field(default=None, max_length=500)
    motivo: str | None = Field(default=None, max_length=500)
    # Fecha del hecho postal que declara el operador. Va DENTRO del cuerpo firmado, así que
    # la autentica el HMAC; es opcional porque no todo operador la manda. Con zona horaria
    # obligatoria: una fecha sin zona no es una fecha de entrega.
    ocurrido_at: datetime | None = None

    @field_validator("ocurrido_at")
    @classmethod
    def _con_zona(cls, v: datetime | None) -> datetime | None:
        if v is not None and v.tzinfo is None:
            raise ValueError("ocurrido_at debe llevar zona horaria")
        return v


class CallbackResult(BaseModel):
    """Resultado del callback. `duplicate` = ya procesado (idempotente); `applied`
    = transición aplicada; `recorded_no_change` = notificación registrada sin
    cambiar el estado (misma etiqueta o transición no válida sobre un terminal);
    `deferred` = la entidad está suspendida: la firma es válida y el cuerpo quedó en
    cola (HTTP 202) para aplicarse al reactivarse (B10, ADR-035 §7)."""

    status: Literal["applied", "duplicate", "recorded_no_change", "deferred"]
    shipment_id: uuid.UUID | None = None
    estado: str | None = None


class TrackingResponse(BaseModel):
    shipment_id: uuid.UUID
    operador: str
    guia: str
    estado_interno_actual: str
    estado_operador: str | None = None
    estado_interno_mapeado: str | None = None
    operador_conectado: bool = False
