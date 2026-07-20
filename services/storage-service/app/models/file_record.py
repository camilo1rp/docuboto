# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass
class FileRecord:
    id: uuid.UUID
    filename: str
    mime_type: str | None
    file_size: int
    sha256: str
    bucket: str
    object_key: str
    uploaded_by: uuid.UUID | None
    uploaded_at: datetime
    version: int = 1
    is_current: bool = True
    root_id: uuid.UUID | None = None
    replaces_id: uuid.UUID | None = None
    # Soft-delete fields (D-04, constitución inv. 12).
    # NULL → active; NOT NULL → logically deleted (pending TRD disposition).
    deleted_at: datetime | None = None
    deleted_by: uuid.UUID | None = None
    # Veredicto PDF/A durable del objeto (E10 C2b follow-up, ingesta E07,
    # migración 013). NULL/"pendiente" = no terminal (ver
    # `app/services/pdfa_ingest_service.py`); reusado por
    # `AipService.empaquetar_aip` si el perfil coincide.
    pdfa_status: str | None = None
    pdfa_profile: str | None = None
    pdfa_checked_at: datetime | None = None
