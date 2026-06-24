# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass
class Notification:
    id: str
    recipient_email: str
    subject: str
    body: str
    status: str  # 'sent' | 'failed'
    sent_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    event_type: str | None = None
    tenant_slug: str | None = None
    attempts: int = 1
    last_error: str | None = None

    def to_redis_hash(self) -> dict[str, str]:
        return {
            "id": self.id,
            "recipient_email": self.recipient_email,
            "subject": self.subject,
            "body": self.body,
            "status": self.status,
            "sent_at": self.sent_at,
            "event_type": self.event_type or "",
            "tenant_slug": self.tenant_slug or "",
        }

    @classmethod
    def from_redis_hash(cls, data: dict[str, str]) -> Notification:
        return cls(
            id=data["id"],
            recipient_email=data["recipient_email"],
            subject=data["subject"],
            body=data["body"],
            status=data["status"],
            sent_at=data["sent_at"],
            event_type=data.get("event_type") or None,
            tenant_slug=data.get("tenant_slug") or None,
        )
