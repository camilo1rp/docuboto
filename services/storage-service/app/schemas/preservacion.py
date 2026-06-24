# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas de preservación digital (E10, ADR/AGN 001/2024)."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

_TIPO = Literal["INGESTA", "FIJACION", "MIGRACION", "VALIDACION_PDFA", "WORM", "REPLICA"]


class PlanCreate(BaseModel):
    formatos_destino: list[str] = Field(default_factory=list)
    num_copias: int = Field(default=1, ge=1)
    periodicidad_fixity_dias: int = Field(default=180, ge=1)
    politica_migracion: str | None = None
    contingencia: str | None = None


class PlanResponse(BaseModel):
    id: uuid.UUID
    version: int
    formatos_destino: list[str]
    num_copias: int
    periodicidad_fixity_dias: int
    politica_migracion: str | None
    contingencia: str | None
    creado_por: str | None
    created_at: datetime


class EventoCreate(BaseModel):
    object_ref: str = Field(..., min_length=1, max_length=255)
    tipo: _TIPO
    resultado: Literal["ok", "fallo"] = "ok"
    hash_before: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    hash_after: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")
    detalle: dict[str, Any] = Field(default_factory=dict)


class AipDocInput(BaseModel):
    document_id: str = Field(..., min_length=1)
    nombre: str = Field(..., min_length=1, max_length=255)
    valor_huella: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")


class AipCreate(BaseModel):
    expediente_id: str = Field(..., min_length=1)
    documentos: list[AipDocInput] = Field(default_factory=list)
    indice_sha256: str | None = Field(default=None, pattern=r"^[0-9a-fA-F]{64}$")


class AipResponse(BaseModel):
    id: uuid.UUID
    expediente_id: str
    aip_sha256: str
    indice_sha256: str | None
    num_documentos: int
    estado: str
    created_at: datetime
    bagit: str
    premis_xml: str


class EventoResponse(BaseModel):
    id: uuid.UUID
    object_ref: str
    tipo: str
    resultado: str
    hash_before: str | None
    hash_after: str | None
    detalle: dict[str, Any]
    agente: str | None
    ts: datetime
