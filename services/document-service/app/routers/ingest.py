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

Trae los correos no leídos de la bandeja configurada, crea un radicado de
Entrada por cada uno y los marca como leídos. Si IMAP no está configurado,
responde 503.

**Dos controles que faltaban (11.9).** El endpoint solo exigía un JWT válido, y
eso no basta para lo que hace:

1. **RBAC.** Radicar en masa es un acto administrativo, no una operación de
   cualquier usuario con sesión: ahora exige ``USUA_PERM_ADMIN``.
2. **Dueño del buzón.** El proceso tiene UN solo buzón IMAP, pero el tenant
   destino venía de la cabecera. Un usuario de la entidad A podía disparar la
   ingesta y llevarse a su entidad correos dirigidos a la B — y como el poll los
   marca como leídos, el correo no vuelve a aparecer: no es una lectura
   indebida, es una apropiación irreversible. Ahora el buzón declara su entidad
   (``IMAP_TENANT_SLUG``) y una petición desde otra recibe 403.

Además, cada poll deja asiento en ``audit_log``: la ingesta CREA radicados
oficiales, y hasta ahora no quedaba registro de quién la disparó.

El endpoint sigue estando hoy inerte (sin IMAP configurado no hace nada), pero
lo estaría igualmente con estos huecos abiertos el día que E19 se active — que
es cuando dejarían de ser teóricos.
"""

import logging
import uuid
from typing import Any

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status
from orpycamcp_common import audit

from app.clients.imap_client import ImapClient
from app.core.clearance import require_permission
from app.core.config import settings
from app.core.database import get_redis, get_tenant_client, get_tenant_conn
from app.schemas.ingest import PollResult
from app.services.document_service import DocumentService, InvalidTenantError
from app.services.email_ingest import EmailIngestor

logger = logging.getLogger(__name__)

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


def _exigir_dueno_del_buzon(slug: str) -> None:
    """El buzón es UNO y el tenant venía de la cabecera (ver módulo).

    Sin `IMAP_TENANT_SLUG` se responde 503 aunque IMAP esté configurado: un
    buzón sin dueño declarado no tiene forma de saber en qué entidad radicar, y
    adivinarlo por la cabecera es exactamente el defecto. Fail-closed.
    """
    dueno = settings.imap_tenant_slug.strip()
    if not dueno:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "imap_tenant_not_configured",
                "detail": (
                    "La ingesta de correo no declara a qué entidad pertenece el buzón "
                    "(IMAP_TENANT_SLUG)"
                ),
                "status": 503,
            },
        )
    if slug != dueno:
        # No se dice de quién ES el buzón: el mensaje lo leería un usuario de
        # otra entidad, y el nombre de las demás instituciones del despliegue no
        # es asunto suyo.
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "imap_tenant_mismatch",
                "detail": "El buzón de ingesta no pertenece a esta entidad",
                "status": 403,
            },
        )


@router.post(
    "/email/poll",
    response_model=PollResult,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)
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
    slug = _tenant_slug(request)
    _exigir_dueno_del_buzon(slug)
    try:
        service = DocumentService(conn, slug, tenant_client, redis)
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

    await _auditar_poll(conn, request, slug, tracking_numbers)
    return PollResult(ingested=len(tracking_numbers), tracking_numbers=tracking_numbers)


async def _auditar_poll(
    conn: asyncpg.Connection, request: Request, slug: str, tracking_numbers: list[str]
) -> None:
    """Un asiento por poll — no uno por correo.

    Los radicados individuales ya dejan el suyo al crearse; lo que faltaba era
    el acto de disparar la ingesta: quién la ejecutó y cuántos números de
    radicado consumió. Es best-effort, como el resto de trazas de lectura del
    servicio: un fallo de auditoría no convierte en 500 una ingesta que ya
    escribió en la base y ya marcó los correos como leídos.
    """
    crudo = request.headers.get("X-User-Id")
    try:
        actor = str(uuid.UUID(crudo)) if crudo else None
    except ValueError:
        actor = None
    if actor is None:
        return
    try:
        await audit.append(
            conn,
            tenant_slug=slug,
            service="document-service",
            action="ingest.email.poll",
            object_type="ingesta_correo",
            object_ref=settings.imap_mailbox,
            actor=actor,
            canal=request.headers.get("X-Canal") or "system",
            # Los números de radicado sí van: son identificadores oficiales, no
            # contenido. El asunto y el remitente de los correos NO — quedan en
            # el asiento de cada radicado, con su nivel de clasificación.
            payload={"ingeridos": len(tracking_numbers), "radicados": tracking_numbers},
        )
    except Exception:
        logger.warning("audit.append (ingest.email.poll) failed", exc_info=True)
