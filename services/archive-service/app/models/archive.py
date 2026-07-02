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
