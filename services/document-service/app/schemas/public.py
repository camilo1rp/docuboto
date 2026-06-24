# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas de consulta pública por código de verificación (E13) y PQRS (E20/legal)."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class PublicVerifyResponse(BaseModel):
    """Trazabilidad mínima NO sensible expuesta sin autenticación."""

    tracking_number: str
    doc_type: str
    status: str
    registered_at: datetime
    dest_dept: str | None = None


class PqrsCreate(BaseModel):
    """Radicación ciudadana (Ley 1755/2015): petición/queja/reclamo/sugerencia/denuncia."""

    tipo: Literal["peticion", "queja", "reclamo", "sugerencia", "denuncia"] = "peticion"
    nombre: str = Field(..., min_length=1, max_length=255)
    identificacion: str | None = Field(default=None, max_length=40)
    email: str | None = Field(default=None, max_length=255)
    asunto: str = Field(..., min_length=1, max_length=2000)
    descripcion: str = Field(..., min_length=1)


class PqrsResponse(BaseModel):
    """Lo que recibe el ciudadano para hacer seguimiento (E13)."""

    radicado_id: uuid.UUID
    tracking_number: str
    verification_code: str
    mensaje: str = "Su petición fue radicada. Conserve el código de verificación."
