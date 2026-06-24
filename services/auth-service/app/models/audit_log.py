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
from datetime import datetime


@dataclass
class AuditLog:
    event_type: str
    id: uuid.UUID = field(default_factory=uuid.uuid4)
    username: str | None = None
    tenant_slug: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    detail: str | None = None
    created_at: datetime | None = None
