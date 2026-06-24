# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class FileResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    mime_type: str | None
    file_size: int
    sha256: str
    uploaded_at: datetime
    version: int = 1
    is_current: bool = True


class PresignedUrlResponse(BaseModel):
    url: str
    expires_in: int


class IntegrityResponse(BaseModel):
    file_id: uuid.UUID
    expected_sha256: str
    actual_sha256: str
    integrity_ok: bool


class HealthResponse(BaseModel):
    status: str
    database: str
    storage: str
