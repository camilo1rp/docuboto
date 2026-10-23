# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Schemas de la desclasificación con segunda persona (C4, ADR-039)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.core.causales_reserva import (
    PLAZO_MAX_MESES,
    Causal,
    CausalCodigo,
    validar_cita_norma,
    validar_clasificacion,
)


class DesclasificacionRequest(BaseModel):
    """Solicitud de bajar un radicado CLASIFICADO (nivel 3) a un nivel menor."""

    nivel_seguridad: int = Field(..., ge=1, le=2, description="Nivel de destino (1 o 2)")
    motivo: str = Field(..., min_length=10, max_length=500)
    # Solo si el destino es 2 (reservada): la causal del art. 19, su norma especial y
    # el plazo. Mismas reglas que `ReclassifyRequest`.
    causal_reserva: CausalCodigo | None = None
    fundamento_juridico: str | None = Field(default=None, max_length=300)
    plazo_reserva_meses: int | None = Field(default=None, ge=1, le=PLAZO_MAX_MESES)
    # Cita normativa PUBLICABLE y controlada por formato (Decreto 103/2015 art. 40).
    fundamento_norma: str | None = Field(default=None, max_length=160)

    @model_validator(mode="after")
    def _destino_motivado(self) -> DesclasificacionRequest:
        if not self.motivo.strip() or len(self.motivo.strip()) < 10:
            raise ValueError("motivo debe tener al menos 10 caracteres")
        error = validar_clasificacion(
            self.nivel_seguridad, self.causal_reserva, self.plazo_reserva_meses
        )
        if error is not None:
            raise ValueError(error)
        if self.fundamento_norma is not None:
            if self.nivel_seguridad < 2:
                raise ValueError("fundamento_norma solo aplica si el destino es nivel 2")
            error = validar_cita_norma(self.fundamento_norma)
            if error is not None:
                raise ValueError(error)
        if self.nivel_seguridad >= 2 and not (
            self.fundamento_juridico and self.fundamento_juridico.strip()
        ):
            raise ValueError(
                "fundamento_juridico es obligatorio si el destino es nivel 2 (reservada)"
            )
        return self


class DesclasificacionRechazo(BaseModel):
    resolucion_motivo: str = Field(..., min_length=10, max_length=500)


class DesclasificacionRetiro(BaseModel):
    motivo: str | None = Field(default=None, max_length=500)


class DesclasificacionResponse(BaseModel):
    id: uuid.UUID
    radicado_id: uuid.UUID
    nivel_anterior: int
    nivel_nuevo: int
    motivo: str
    causal_reserva: str | None
    fundamento_juridico: str | None
    fundamento_norma: str | None = None
    plazo_reserva_meses: int | None
    #: Estado guardado.
    estado: str
    #: Estado a hoy: una `pendiente` cuyo `vence_at` pasó se lee `caducada` aunque
    #: nadie la haya materializado todavía.
    estado_efectivo: str
    solicitado_por: uuid.UUID
    solicitado_at: datetime
    vence_at: datetime
    resuelto_por: uuid.UUID | None = None
    resuelto_at: datetime | None = None
    resolucion_motivo: str | None = None
    #: Número del radicado; solo en el historial y la cola (el llamante alcanza el radicado).
    tracking_number: str | None = None


class DesclasificacionAprobada(DesclasificacionResponse):
    """Respuesta de aprobar: la solicitud resuelta Y el nivel con que quedó el radicado."""

    nivel_seguridad: int


class DesclasificacionPendiente(DesclasificacionResponse):
    """Fila de la cola de decisión (`GET /api/v1/desclasificaciones`)."""

    subject: str | None = None
    doc_type: str | None = None
    #: Nivel actual del radicado cuyo asunto trae la fila (la barrera de residencia recorta por él).
    nivel_seguridad: int
    #: La solicitó el propio llamante: no puede decidirla (solo retirarla).
    propia: bool = False


class CausalReserva(BaseModel):
    codigo: str
    articulo: str
    literal: str
    descripcion: str
    nivel: Literal[2, 3]
    categoria: Literal["clasificada", "reservada"]
    #: `None` = sin plazo (art. 18); con valor = máximo de meses, obligatorio.
    plazo_max_meses: int | None

    @classmethod
    def de(cls, c: Causal) -> CausalReserva:
        return cls(
            codigo=c.codigo,
            articulo=c.articulo,
            literal=c.literal,
            descripcion=c.descripcion,
            nivel=c.nivel,
            categoria=c.categoria,
            plazo_max_meses=c.plazo_max_meses,
        )


class CausalesReservaResponse(BaseModel):
    #: Nivel desde el que BAJAR exige segunda persona (`NIVEL_DOBLE_FIRMA`).
    nivel_doble_firma: int
    plazo_max_meses: int
    causales: list[CausalReserva]
