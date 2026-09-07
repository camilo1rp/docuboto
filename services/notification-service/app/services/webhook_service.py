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

import asyncio
import hashlib
import hmac
import json
import logging

import httpx

from app.core.config import settings
from app.core.database import acquire_tenant
from app.core.webhook_security import validate_webhook_target
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
        # Re-valida el destino en CADA intento (no solo al crear la
        # suscripción): cierra el caso de DNS rebinding (un dominio que
        # resolvía a IP pública al suscribirse y luego se re-apunta a una IP
        # interna) y también cubre suscripciones PREEXISTENTES a la política
        # (creadas antes de este endurecimiento, o con
        # webhook_allow_private_targets=True en su momento). Una suscripción
        # rechazada aquí NO se borra ni se desactiva — sigue existiendo,
        # simplemente no entrega mientras su destino sea inválido; el
        # administrador decide (ver DELETE /api/v1/webhooks/{id}). Queda un
        # TOCTOU residual y aceptado: ver docstring de
        # app.core.webhook_security.
        try:
            await asyncio.to_thread(
                validate_webhook_target,
                sub["url"],
                allow_private=settings.webhook_allow_private_targets,
            )
        except ValueError as exc:
            logger.warning("webhook %s destino rechazado, no se entrega: %s", sub["url"], exc)
            return False
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


# ── Lo clasificado no sale por un webhook ───────────────────────────────────
#
# `dispatch` serializaba el sobre ENTERO y lo enviaba a cada suscripción activa
# sin mirar una sola vez la clasificación del objeto al que el evento se refiere.
# El payload de una radicación lleva el asunto, el nombre y la entidad del
# remitente; el de una reclasificación lleva además la causal de reserva y su
# fundamento jurídico. Una sola suscripción con la lista de tipos vacía —que
# significa «todos»— se lo llevaba todo a una dirección externa, y darla de alta
# solo exige el permiso de administración, que no es lo mismo que tener nivel de
# acceso sobre lo reservado.
#
# La contradicción estaba escrita dentro de este mismo servicio: el resumen del
# dead-letter dice que NO expone el payload ni el número de radicado porque
# revelarlos filtraría la existencia de lo clasificado. Ese criterio no había
# llegado hasta aquí.
#
# Se recorta el CONTENIDO y se conserva el sobre: quien recibe sigue sabiendo
# QUÉ pasó y CUÁNDO, que es para lo que se integra, y deja de recibir el texto
# de un documento reservado. Es el mismo criterio que la barrera del asistente.

#: Desde RESERVADA (2) el contenido no sale a un destino externo.
_NIVEL_RESERVADA = 2

#: Campos del payload que llevan contenido o datos personales. Se escribe por
#: ADMISIÓN inversa —lo que se BORRA— y no por lista de lo que se conserva,
#: porque el sobre gana campos con el tiempo y una lista de permitidos que se
#: queda corta filtra en silencio, mientras que una de prohibidos que se queda
#: corta solo deja de recortar algo que ya se sabía sensible.
_CAMPOS_CON_CONTENIDO = (
    "subject",
    "asunto",
    "sender_name",
    "sender_entity",
    "remitente",
    "observations",
    "cuerpo",
    "causal_reserva",
    "fundamento_juridico",
    "ciudadano",
    "metadata",
)

_AVISO_RECORTE = (
    "Contenido omitido: el objeto de este evento está clasificado y su texto no "
    "sale a un destino externo. Consúltelo en el sistema."
)


def recortar_si_clasificado(envelope: dict) -> dict:
    """Devuelve el sobre sin contenido si el objeto está clasificado.

    Decide por el `nivel_seguridad` que el propio payload declara: no depende de
    qué tipo de evento sea ni de que alguien recuerde añadir el siguiente a una
    lista.
    """
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        return envelope

    nivel = payload.get("nivel_seguridad")
    if not isinstance(nivel, int) or nivel < _NIVEL_RESERVADA:
        return envelope

    recortado = {k: v for k, v in payload.items() if k not in _CAMPOS_CON_CONTENIDO}
    recortado["_recorte"] = _AVISO_RECORTE
    return {**envelope, "payload": recortado}


async def dispatch(envelope: dict) -> int:
    """Entrega el sobre a las suscripciones del tenant. Devuelve cuántas se intentaron.

    Cualquier fallo se registra y se traga: nunca rompe el procesamiento del evento.
    """
    tenant_slug = envelope.get("tenant_slug")
    event_type = envelope.get("event_type")
    if not tenant_slug or not event_type:
        return 0
    try:
        body = json.dumps(
            recortar_si_clasificado(envelope), default=str, sort_keys=True
        ).encode()
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
