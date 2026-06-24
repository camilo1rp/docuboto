# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Ingesta de correo: endpoint de poll (E19, F2).

Operación interna/administrativa (requiere JWT vía gateway). Trae los correos no
leídos de la bandeja configurada, crea un radicado de Entrada por cada uno y los
marca como leídos. El tenant destino viaja en `X-Tenant-Slug`. Si IMAP no está
configurado, responde 503.
"""

from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.clients.imap_client import ImapClient
from app.core.database import get_redis, get_tenant_client, get_tenant_conn
from app.schemas.ingest import PollResult
from app.services.document_service import DocumentService, InvalidTenantError
from app.services.email_ingest import EmailIngestor

router = APIRouter(prefix="/api/v1/ingest", tags=["ingest"])


def get_imap_client(request: Request) -> ImapClient | None:
    """Cliente IMAP configurado en el lifespan (None si IMAP no está habilitado)."""
    return getattr(request.app.state, "imap_client", None)


def _tenant_slug(request: Request) -> str:
    slug = request.headers.get("X-Tenant-Slug", "")
    if not slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "missing_tenant", "detail": "X-Tenant-Slug requerido", "status": 400},
        )
    return slug


@router.post("/email/poll", response_model=PollResult)
async def poll_email(
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
    tenant_client: Any = Depends(get_tenant_client),
    redis: Any = Depends(get_redis),
    imap: ImapClient | None = Depends(get_imap_client),
) -> PollResult:
    if imap is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "email_not_configured",
                "detail": "La ingesta de correo no está habilitada (IMAP sin configurar)",
                "status": 503,
            },
        )
    try:
        service = DocumentService(conn, _tenant_slug(request), tenant_client, redis)
    except InvalidTenantError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "invalid_tenant_slug", "detail": str(exc), "status": 400},
        )

    ingestor = EmailIngestor(service)
    tracking_numbers: list[str] = []
    for msg in await imap.fetch_unseen():
        tracking_numbers.append(await ingestor.ingest(msg))
        await imap.mark_seen(msg.uid)
    return PollResult(ingested=len(tracking_numbers), tracking_numbers=tracking_numbers)
