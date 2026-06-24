# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Ingesta de correo → radicado de Entrada (E19, F2).

Mapea un `EmailMessage` a un `DocumentCreate` (doc_type='E') y delega en
`DocumentService.create`, reutilizando la numeración atómica, la auditoría y los
eventos de la radicación normal. El correo del remitente y los nombres de los
anexos se guardan en `metadata` (no hay columna dedicada). La subida de los
ficheros adjuntos a MinIO (storage-service) queda como evolución posterior.
"""

from __future__ import annotations

from app.clients.imap_client import EmailMessage
from app.schemas.document import DocumentCreate
from app.services.document_service import DocumentService

_SUBJECT_FALLBACK = "(correo sin asunto)"
_MAX_OBS = 4000


class EmailIngestor:
    def __init__(self, service: DocumentService) -> None:
        self._service = service

    def _to_payload(self, msg: EmailMessage) -> DocumentCreate:
        subject = (msg.subject or _SUBJECT_FALLBACK)[:2000]
        return DocumentCreate(
            doc_type="E",
            subject=subject,
            sender_name=(msg.from_name or msg.from_addr or None),
            observations=(msg.body or None) and msg.body[:_MAX_OBS],
            metadata={
                "source": "email",
                "sender_email": msg.from_addr,
                "attachments": msg.attachments,
            },
        )

    async def ingest(self, msg: EmailMessage) -> str:
        """Crea el radicado de Entrada y devuelve su número de radicado."""
        result = await self._service.create(self._to_payload(msg), registered_by=None)
        return result.tracking_number
