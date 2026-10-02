# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""CRUD de suscripciones de webhooks (E11, ADR-018)."""

import hashlib
import uuid
from urllib.parse import urlsplit

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Response, status

from app.core import webhook_secrets
from app.core.clearance import get_actor, require_permission
from app.core.database import get_tenant_conn
from app.repositories.webhook_repository import WebhookRepository
from app.schemas.webhook import WebhookCreate, WebhookResponse

try:
    from orpycamcp_common import audit as _audit

    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover
    _audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

_SERVICE = "notification-service"

# Config de webhooks salientes (E11, ADR-018): registrar una suscripción entrega
# CADA evento del tenant que case a una URL externa — un canal de exfiltración (y
# SSRF) si cualquiera puede crearlo. Es configuración de administración: el CRUD
# exige USUA_PERM_ADMIN; listar basta con nivel 1, pero crear y borrar (que
# abren/cierran el canal de salida) exigen nivel 3 y dejan asiento en audit_log
# con el actor, atómico con la escritura (OMI-03).
router = APIRouter(
    prefix="/api/v1/webhooks",
    tags=["webhooks"],
)

_NIVEL_ESCRITURA = 3


def _url_sin_credenciales(url: str) -> str:
    """El asiento es inmutable: ni `user:pass@`, ni query, ni PATH (Slack/Teams/Discord
    llevan el token ahí). Solo scheme://host:port; el hash correlaciona sin exponer."""
    p = urlsplit(url)
    host = p.hostname or ""
    host = f"[{host}]" if ":" in host else host
    puerto = f":{p.port}" if p.port else ""
    return f"{p.scheme}://{host}{puerto}"


def _huella_url(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()


def _exigir_auditoria() -> None:
    # Fail-closed: abrir o cerrar el canal de salida sin traza legal no es admisible.
    if not _AUDIT_AVAILABLE:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "audit_unavailable",
                "detail": "El módulo de auditoría no está disponible; se rechaza el cambio.",
                "status": 503,
            },
        )


@router.post(
    "",
    response_model=WebhookResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=_NIVEL_ESCRITURA))],
)
async def create_webhook(
    payload: WebhookCreate,
    actor: uuid.UUID = Depends(get_actor),
    x_tenant_slug: str | None = Header(default=None),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> WebhookResponse:
    _exigir_auditoria()
    # Fail-closed: sin KEK utilizable no se acepta un secreto (nunca en claro).
    # El `id` se acuña aquí porque el AAD del cifrado lo incluye.
    webhook_id = uuid.uuid4()
    ct: bytes | None = None
    nonce: bytes | None = None
    kek_version: int | None = None
    if payload.secret is not None:
        try:
            ct, nonce, kek_version = webhook_secrets.encrypt_secret(
                payload.secret, tenant_slug=x_tenant_slug or "", webhook_id=webhook_id
            )
        except RuntimeError:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "webhook_secret_kek_unavailable",
                    "detail": "La clave de cifrado de secretos de webhook no está disponible.",
                    "status": 503,
                },
            ) from None
    async with conn.transaction():
        row = await WebhookRepository(conn).insert(
            webhook_id=webhook_id,
            url=str(payload.url),
            event_types=payload.event_types,
            secret_cifrado=ct,
            secret_nonce=nonce,
            secret_kek_version=kek_version,
        )
        await _audit.append(
            conn,
            tenant_slug=x_tenant_slug,
            service=_SERVICE,
            action="notification.webhook.created",
            object_type="webhook",
            object_ref=str(row["id"]),
            actor=str(actor),
            payload={
                "url": _url_sin_credenciales(str(payload.url)),
                "url_sha256": _huella_url(str(payload.url)),
                "event_types": payload.event_types,
                "con_secret": payload.secret is not None,
            },
        )
    return WebhookResponse(**dict(row))


@router.get(
    "",
    response_model=list[WebhookResponse],
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)
async def list_webhooks(
    response: Response,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[WebhookResponse]:
    rows = await WebhookRepository(conn).list()
    response.headers["X-Total-Count"] = str(len(rows))
    return [WebhookResponse(**dict(r)) for r in rows]


@router.delete(
    "/{webhook_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN", min_crud=_NIVEL_ESCRITURA))],
)
async def delete_webhook(
    webhook_id: uuid.UUID,
    actor: uuid.UUID = Depends(get_actor),
    x_tenant_slug: str | None = Header(default=None),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> None:
    _exigir_auditoria()
    async with conn.transaction():
        row = await WebhookRepository(conn).delete(webhook_id)
        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "webhook_not_found", "detail": str(webhook_id), "status": 404},
            )
        await _audit.append(
            conn,
            tenant_slug=x_tenant_slug,
            service=_SERVICE,
            action="notification.webhook.deleted",
            object_type="webhook",
            object_ref=str(webhook_id),
            actor=str(actor),
            payload={
                "url": _url_sin_credenciales(row["url"]),
                "url_sha256": _huella_url(row["url"]),
                "event_types": list(row["event_types"]),
            },
        )
