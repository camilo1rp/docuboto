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
    # RF-FIR-13: True cuando `body` ya llegó redactado (ver
    # notification_service.REDACTED_BODY) porque el envío original era
    # sensible (p. ej. OTP de 2FA, E06 Inc.2). El envío SMTP real siempre usó
    # el body sin redactar; este flag documenta que ESTA instancia es la
    # copia destinada a persistencia, no el correo enviado.
    sensible: bool = False
