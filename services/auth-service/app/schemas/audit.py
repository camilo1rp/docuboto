# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas de consulta y verificación de auditoría (E08, ADR-008)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel


class AuditEntry(BaseModel):
    id: int
    ts: datetime
    service: str
    actor: str | None
    canal: str
    action: str
    object_type: str
    object_ref: str | None
    payload: dict[str, Any]
    hash: str


class AuditVerifyResponse(BaseModel):
    tenant_slug: str
    ok: bool
    broken_id: int | None = None
