# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas de clasificación de seguridad (E08, RF-SEG-08)."""

from pydantic import BaseModel, Field


class SecurityLevel(BaseModel):
    code: int
    nombre: str


class ClearanceSet(BaseModel):
    max_level: int = Field(..., ge=1, le=3)


class CallerClearance(BaseModel):
    user_id: str
    max_level: int
    accessible_levels: list[int]


class ClearanceCheck(BaseModel):
    level: int
    max_level: int
    allowed: bool
