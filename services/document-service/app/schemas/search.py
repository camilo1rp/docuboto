# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas for full-text search results."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class SearchHit(BaseModel):
    id: UUID
    tracking_number: str
    doc_type: str
    subject: str
    sender_name: str | None
    sender_entity: str | None
    # Tipología documental. Viaja en el resultado de búsqueda porque quien
    # vincula un radicado a un expediente la necesita para aportarla al índice
    # electrónico (E15 perfil v4 / RT-15): archive-service no lee `radicados`,
    # así que si no sale por aquí no hay forma de que llegue al <tipologia>.
    doc_class: str | None = None
    dest_dept: str | None
    # Dónde está el radicado AHORA (ADR-029); `dest_dept` es la de radicación.
    ubicacion_dept_code: int | None = None
    ubicacion_dept: str | None = None
    status: str
    registered_at: datetime
    rank: float = Field(description="Relevance score (0.0 - 1.0)")


class SearchResponse(BaseModel):
    query: str | None = None
    total: int
    hits: list[SearchHit]
    page: int
    size: int
