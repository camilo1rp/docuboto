# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass
class Expediente:
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    trd_serie_id: uuid.UUID | None
    status: str
    opened_by: uuid.UUID | None
    opened_at: datetime
    closed_at: datetime | None
    updated_at: datetime
    metadata: dict = field(default_factory=dict)
    metadata_template_id: uuid.UUID | None = None
    disposition: dict | None = None
    radicados: list[ExpedienteRadicado] = field(default_factory=list)
    # Nivel de seguridad (RF-SEG-08, 013_expediente_security_level.sql): hasta
    # el perfil v3 del índice (E15) era write-only aquí — se persistía pero
    # nunca se leía de vuelta al dataclass. El índice electrónico lo necesita
    # para <listaControlAcceso> por documento (heredado del expediente).
    nivel_seguridad: int = 1


@dataclass
class ExpedienteRadicado:
    expediente_id: uuid.UUID
    radicado_id: uuid.UUID
    tracking_number: str
    added_at: datetime
    content_hash: str | None = None
    # Metadatos RT-15 del índice electrónico (E15 Inc.3): aportados al vincular.
    formato: str | None = None
    tamano_bytes: int | None = None
    folios: int | None = None
    # E15 finalización RT-15 (perfil v3, migración 018): orden_documental
    # ESTABLE asignado UNA vez al vincular (MAX+1 por expediente, atómico) —
    # inmutable, nunca se renumera ni se reutiliza. `estado` distingue el
    # vínculo vigente ('vinculado') del excluido por soft-delete
    # ('excluido'); la fila permanece para que el índice muestre el hueco
    # trazable (Acuerdo AGN 001/2024 art. 4.3.2.3).
    orden_documental: int | None = None
    estado: str = "vinculado"
    excluido_at: datetime | None = None
    excluido_causal: str | None = None


@dataclass
class TrdSerie:
    id: uuid.UUID
    code: str
    name: str
    description: str | None
    retention_years: int
    total_retention: int
    disposition: str
    is_active: bool
    created_at: datetime
    # E04/ADR-015: jerarquía + retención en dos fases + versionado.
    parent_id: uuid.UUID | None = None
    archivo_gestion_years: int | None = None
    archivo_central_years: int | None = None
    version: int = 1
    valid_from: date | None = None
