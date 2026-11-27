# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Contratos de la retención legal por motivo y de la valoración de la TVD (C-5 / C7, ADR-043)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

# `valoracion_pendiente` es un hold DE SISTEMA: lo fija archive al proteger un fondo vencido y lo
# levanta la valoración del Comité. Ningún usuario lo fija ni lo levanta a mano.
MotivoHold = Literal[
    "judicial",
    "disciplinario",
    "fiscal",
    "administrativo",
    "ddhh_memoria_historica",
    "valoracion_pendiente",
]

#: Criterio de levantamiento de la retención por DDHH / memoria histórica (Ley 1448/2011
#: art. 144 [confirmar]): ni el vencimiento de la retención de la TRD ni una valoración la
#: levantan; solo un acto de autoridad competente, explicado con holgura. Por eso su
#: fundamento de levantamiento exige más que el mínimo común.
FUNDAMENTO_MIN_LEVANTAR_DDHH = 50


class LegalHoldFijarRequest(BaseModel):
    """`POST /expedientes/{id}/legal-holds`. Motivo, fundamento y acta son obligatorios: un freeze
    que bloquea la disposición final sin acto que lo respalde es un bloqueo arbitrario."""

    model_config = ConfigDict(extra="forbid")

    motivo_tipo: MotivoHold
    fundamento: str = Field(..., min_length=10, max_length=2000)
    acta_ref: str = Field(..., min_length=1, max_length=255)


class LegalHoldLevantarRequest(BaseModel):
    """`POST /expedientes/{id}/legal-holds/{hold_id}/levantar`. `motivo_tipo` es la CONFIRMACIÓN
    del motivo del hold que se levanta: si no coincide, 409 `motivo_no_coincide` (un acto no
    levanta el hold de otro)."""

    model_config = ConfigDict(extra="forbid")

    motivo_tipo: MotivoHold
    fundamento: str = Field(..., min_length=10, max_length=2000)
    acta_ref: str = Field(..., min_length=1, max_length=255)

    @model_validator(mode="after")
    def _criterio_ddhh(self) -> LegalHoldLevantarRequest:
        if (
            self.motivo_tipo == "ddhh_memoria_historica"
            and len(self.fundamento.strip()) < FUNDAMENTO_MIN_LEVANTAR_DDHH
        ):
            raise ValueError(
                "levantar una retención por DDHH / memoria histórica exige el acto de la "
                f"autoridad competente explicado en al menos {FUNDAMENTO_MIN_LEVANTAR_DDHH} caracteres"
            )
        return self


class LegalHoldItem(BaseModel):
    hold_id: uuid.UUID
    motivo_tipo: MotivoHold
    fundamento: str
    acta_ref: str
    fijado_por: uuid.UUID | None = None
    fijado_at: datetime
    levantado_por: uuid.UUID | None = None
    levantado_at: datetime | None = None
    levantado_fundamento: str | None = None
    levantado_acta_ref: str | None = None


class LegalHoldFijado(BaseModel):
    hold_id: uuid.UUID
    objetos_afectados: int


class LegalHoldLevantado(BaseModel):
    hold_id: uuid.UUID
    # Objetos cuyo legal hold se liberó en el almacén; 0 mientras quede otro hold activo.
    objetos_liberados_s3: int
    holds_restantes: int


# -- valoración de la TVD ----------------------------------------------------------------------


class ValoracionTvdRequest(BaseModel):
    """`POST /tvd/{serie_id}/valoracion`: el acto del Comité sobre UNA versión de TVD."""

    model_config = ConfigDict(extra="forbid")

    acta_comite: str = Field(..., min_length=1, max_length=255)
    fecha_acta: date
    fundamento: str = Field(..., min_length=20, max_length=4000)
    # Selección (S): cómo se elige la muestra que se conserva (aleatorio, por criterio, por
    # unidad…). Obligatorio con disposición S (el servicio responde 422 si falta: la disposición
    # la fija la TVD, no el cuerpo); se ignora con las demás.
    metodo_muestreo: str | None = Field(default=None, min_length=10, max_length=2000)


class ValoracionTvdResponse(BaseModel):
    valoracion_id: uuid.UUID
    disposicion: Literal["CT", "E", "S", "M"]
    # Solo recuento: acto de custodia, ortogonal al clearance (no enumera expedientes).
    expedientes_afectados: int
    # Con disposición S: cuántos esperan el acto de selección (ninguno se levanta solo).
    seleccion_pendiente: int = 0


class ValoracionTvd(BaseModel):
    valoracion_id: uuid.UUID
    trd_serie_id: uuid.UUID
    disposicion: Literal["CT", "E", "S", "M"]
    acta_comite: str
    fecha_acta: date
    fundamento: str
    decidida_por: uuid.UUID
    decidida_at: datetime
    metodo_muestreo: str | None = None


class ValoracionTvdEstado(BaseModel):
    """`GET /tvd/{serie_id}/valoracion`. `valoracion` es `None` mientras el Comité no la registre
    (los `pendientes` esperan ese acto)."""

    trd_serie_id: uuid.UUID
    valoracion: ValoracionTvd | None = None
    pendientes: int
    aplicadas: int
    agotadas: int
    #: Disposición S: valorados que esperan el acto de selección por expediente.
    seleccion_pendiente: int = 0


class SeleccionValoracionRequest(BaseModel):
    """`POST /tvd/{serie_id}/valoracion/expedientes/{expediente_id}/seleccion`: el acto de
    selección (disposición S) sobre UN expediente. `conservar` = entra a la muestra y sigue el
    camino de CT; `descartar` = se levanta la retención legal y queda elegible para el lote."""

    model_config = ConfigDict(extra="forbid")

    accion: Literal["conservar", "descartar"]
    fundamento: str = Field(..., min_length=10, max_length=2000)


class SeleccionValoracionResponse(BaseModel):
    expediente_id: uuid.UUID
    accion: Literal["conservar", "descartar"]
    # `aplicada` | `pendiente` (la fase de reconciliación la completa) | `agotada`.
    aplicacion: str


class SeleccionPendienteItem(BaseModel):
    """Un expediente a la espera de selección. Solo identificador y código: ni título ni
    contenido (acto de custodia ortogonal al clearance, como el recuento)."""

    expediente_id: uuid.UUID
    expediente_code: str


# -- C14: soporte documental de las constancias de firma inobtenible (ADR-043) ------------------


class SoporteCitado(BaseModel):
    """Una constancia de firma inobtenible VIGENTE cuyo anexo de soporte se destruirá con el lote.

    Es la BAJA DE SOPORTE que el Comité avala al avalar el lote. Content-free: identifica la
    constancia (transferencia, rol, ordinal) y el expediente del inventario, nunca el anexo."""

    expediente_id: uuid.UUID
    expediente_code: str
    excepcion_id: uuid.UUID
    transferencia_id: uuid.UUID
    rol: str
    ordinal: int
    # Fijado al ejecutar el lote; `None` antes.
    soporte_eliminado_at: datetime | None = None
