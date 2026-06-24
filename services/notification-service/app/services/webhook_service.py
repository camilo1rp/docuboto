# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Entrega de webhooks salientes firmados (E11, ADR-018).

`dispatch` toma un sobre de evento canónico, busca las suscripciones activas del
tenant que coinciden con su `event_type` y hace POST del sobre con firma
HMAC-SHA256. Es **best-effort con reintentos** y nunca propaga errores al
consumidor del bus (un webhook caído no afecta al SGDEA).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging

import httpx

from app.core.database import acquire_tenant
from app.repositories.webhook_repository import WebhookRepository

logger = logging.getLogger(__name__)

_TIMEOUT = 5.0
_MAX_ATTEMPTS = 3


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def _deliver_one(client: httpx.AsyncClient, sub, body: bytes) -> bool:
    headers = {"Content-Type": "application/json"}
    if sub["secret"]:
        headers["X-OrpycaMCP-Signature"] = sign(sub["secret"], body)
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            resp = await client.post(sub["url"], content=body, headers=headers)
            if resp.status_code < 400:
                return True
            logger.warning(
                "webhook %s → HTTP %s (intento %s)", sub["url"], resp.status_code, attempt
            )
        except httpx.RequestError as exc:
            logger.warning("webhook %s error: %s (intento %s)", sub["url"], exc, attempt)
    return False


async def dispatch(envelope: dict) -> int:
    """Entrega el sobre a las suscripciones del tenant. Devuelve cuántas se intentaron.

    Cualquier fallo se registra y se traga: nunca rompe el procesamiento del evento.
    """
    tenant_slug = envelope.get("tenant_slug")
    event_type = envelope.get("event_type")
    if not tenant_slug or not event_type:
        return 0
    try:
        body = json.dumps(envelope, default=str, sort_keys=True).encode()
        async with acquire_tenant(tenant_slug) as conn:
            subs = await WebhookRepository(conn).active_for_event(event_type)
        if not subs:
            return 0
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            for sub in subs:
                await _deliver_one(client, sub, body)
        return len(subs)
    except Exception as exc:  # noqa: BLE001 — best-effort: no romper el consumidor
        logger.warning("dispatch de webhooks falló para %s: %s", tenant_slug, exc)
        return 0
