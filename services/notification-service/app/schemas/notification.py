# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from __future__ import annotations

from pydantic import BaseModel


class NotificationSend(BaseModel):
    recipient_email: str
    subject: str
    body: str
    tenant_slug: str | None = None


class NotificationResponse(BaseModel):
    id: str
    recipient_email: str
    subject: str
    status: str
    event_type: str | None
    tenant_slug: str | None
    sent_at: str
    attempts: int = 1


class NotificationAccepted(BaseModel):
    id: str
    status: str = "accepted"


class HistoryResponse(BaseModel):
    items: list[NotificationResponse]
    total: int
