# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any


@dataclass
class Anexo:
    id: uuid.UUID
    radicado_id: uuid.UUID
    file_id: uuid.UUID
    filename: str
    file_size: int | None
    mime_type: str | None
    created_at: datetime
    tipo_anexo: str | None = None
    es_principal: bool = False
    folios: int | None = None


@dataclass
class Radicado:
    id: uuid.UUID
    tracking_number: str
    doc_type: str
    year: int
    sequence: int
    subject: str
    doc_class: str | None
    origin_dept: str | None
    dest_dept: str | None
    origin_dept_code: int | None
    dest_dept_code: int | None
    sender_name: str | None
    sender_entity: str | None
    status: str
    pages: int
    nivel_seguridad: int
    observations: str | None
    registered_by: uuid.UUID | None
    registered_at: datetime
    updated_at: datetime
    tenant_code: str
    due_date: date | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    metadata_template_id: uuid.UUID | None = None
    # Esquema de publicación (Ley 1712/2014 art. 12). `publicable` NO se deduce
    # de `nivel_seguridad`: un radicado de nivel público que nadie ha decidido
    # publicar sigue sin publicarse.
    publicable: bool = False
    publicado_at: datetime | None = None
    # Ubicación actual (ADR-029). `flujo_*` las escribe SOLO workflow-service;
    # `ubicacion_*` son columnas generadas (flujo si lo hay, si no radicación).
    # `flujo_usuario` solo se lee en el detalle, nunca en listados.
    flujo_dept_code: int | None = None
    flujo_dept: str | None = None
    flujo_usuario: uuid.UUID | None = None
    ubicacion_dept_code: int | None = None
    ubicacion_dept: str | None = None
    anexos: list[Anexo] = field(default_factory=list)
