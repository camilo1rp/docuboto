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
    dest_dept: str | None
    status: str
    registered_at: datetime
    rank: float = Field(description="Relevance score (0.0 - 1.0)")


class SearchResponse(BaseModel):
    query: str | None = None
    total: int
    hits: list[SearchHit]
    page: int
    size: int
