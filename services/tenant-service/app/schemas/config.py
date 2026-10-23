# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas de configuración por tenant (E14)."""

import uuid
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field


class HolidayCreate(BaseModel):
    fecha: date
    descripcion: str = Field(..., min_length=1, max_length=200)
    tipo: Literal["festivo", "puente", "especial"] = "festivo"
    is_national: bool = True


class HolidayResponse(BaseModel):
    id: uuid.UUID
    fecha: date
    descripcion: str
    tipo: str
    is_national: bool


class HolidaySeedRequest(BaseModel):
    year_from: int = Field(..., ge=2000, le=2100)
    year_to: int = Field(..., ge=2000, le=2100)


class BusinessDaysResult(BaseModel):
    from_date: date
    business_days: int
    due_date: date


class ParamResponse(BaseModel):
    key: str
    value: Any


class ParamUpdate(BaseModel):
    value: Any
    #: Justificación del cambio. OBLIGATORIA cuando el cambio relaja la política
    #: de firma (`firma_personal_require_xades`): `422 motivo_requerido`. Se
    #: asienta en `public.audit_log`. Para el resto de parámetros es opcional.
    motivo: str | None = Field(default=None, max_length=500)


class AsistenteUpdate(BaseModel):
    """Cuerpo de ``PUT /config/asistente``: encender o apagar el asistente (ADR-037).

    Encender es una transmisión de datos a un encargado (Anthropic para el asistente, el
    proveedor de ``rag`` si es externo): exige ``declaracion_transmision: true`` y que la
    entidad tenga publicada su política de tratamiento. Apagar no pide nada.
    """

    habilitado: bool
    #: La entidad declara que la política de tratamiento publicada cubre la transmisión
    #: de datos a los proveedores que muestra ``GET /config/asistente``. Solo se exige al
    #: ENCENDER (``422 declaracion_requerida`` si falta).
    declaracion_transmision: bool = False
    motivo: str | None = Field(default=None, max_length=500)


class ProveedoresAsistente(BaseModel):
    """Proveedores efectivos del asistente, para que quien lo enciende sepa a quién transmite."""

    asistente: str
    modelo: str
    rag: str
    embeddings: str
    embeddings_local: bool


class AsistenteResponse(BaseModel):
    habilitado: bool
    #: ``False`` cuando el valor pedido ya era el vigente (no hubo acto ni asiento).
    cambiado: bool = False
    proveedores: ProveedoresAsistente
