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
from urllib.parse import urlsplit, urlunsplit

import httpx
from cryptography.exceptions import InvalidTag

from app.core import webhook_secrets
from app.core.config import settings
from app.core.database import acquire_tenant
from app.core.webhook_security import validate_webhook_target
from app.repositories.webhook_repository import WebhookRepository

logger = logging.getLogger(__name__)

_TIMEOUT = 5.0
_MAX_ATTEMPTS = 3


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _pinned_request(url: str, ip: str | None) -> tuple[str, dict[str, str], dict[str, str]]:
    """URL con la IP ya validada + cabecera `Host` y SNI originales.

    Cierra el TOCTOU de DNS: `httpx` conecta a la IP que se validó en vez de
    volver a resolver el nombre. Sin IP que fijar (`allow_private`) devuelve la
    URL tal cual.
    """
    if ip is None:
        return url, {}, {}
    parts = urlsplit(url)
    host = parts.hostname or ""
    netloc_host = f"[{ip}]" if ":" in ip else ip
    port = f":{parts.port}" if parts.port else ""
    host_header = f"[{host}]" if ":" in host else host
    pinned = urlunsplit(parts._replace(netloc=f"{netloc_host}{port}"))
    return pinned, {"Host": f"{host_header}{port}"}, {"sni_hostname": host}


class _SecretoNoUtilizable(Exception):
    """La suscripción tiene secreto, pero no se puede firmar con él sin riesgo."""


def _firma_de_la_entrega(sub, body: bytes, tenant_slug: str) -> str | None:
    """Firma HMAC de la entrega, o None si la suscripción no tiene secreto.

    Descifra el secreto SOLO en memoria, solo para firmar este cuerpo, y lo suelta
    al volver. Fail-closed: una fila que aún tiene el secreto en CLARO (heredada,
    pendiente de `python -m app.ops.cifrar_webhook_secrets`) no se entrega ni se
    firma con ese texto —seguiría vivo y expuesto en la base—, y un ciphertext que
    no descifra (KEK ausente/equivocada, AAD de otro tenant o webhook) tampoco. El
    mensaje de error nunca lleva el secreto ni el ciphertext.
    """
    cifrado = sub.get("secret_cifrado")
    if cifrado is None:
        if sub.get("secret_en_claro"):
            raise _SecretoNoUtilizable("secreto aún en claro, falta cifrar los heredados")
        return None
    try:
        secreto = webhook_secrets.decrypt_secret(
            bytes(cifrado),
            nonce=bytes(sub["secret_nonce"]),
            version=int(sub["secret_kek_version"]),
            tenant_slug=tenant_slug,
            webhook_id=sub["id"],
        )
    except (InvalidTag, RuntimeError) as exc:
        raise _SecretoNoUtilizable(f"el secreto no descifra ({type(exc).__name__})") from None
    return sign(secreto, body)


async def _deliver_one(
    client: httpx.AsyncClient, sub, body: bytes, tenant_slug: str = ""
) -> bool:
    headers = {"Content-Type": "application/json"}
    try:
        firma = _firma_de_la_entrega(sub, body, tenant_slug)
    except _SecretoNoUtilizable as exc:
        logger.error("webhook %s no se entrega: %s", sub.get("id"), exc)
        return False
    if firma:
        headers["X-OrpycaMCP-Signature"] = firma
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        # Re-valida el destino en CADA intento (no solo al crear la
        # suscripción): cierra el caso de DNS rebinding (un dominio que
        # resolvía a IP pública al suscribirse y luego se re-apunta a una IP
        # interna) y también cubre suscripciones PREEXISTENTES a la política
        # (creadas antes de este endurecimiento, o con
        # webhook_allow_private_targets=True en su momento). Una suscripción
        # rechazada aquí NO se borra ni se desactiva — sigue existiendo,
        # simplemente no entrega mientras su destino sea inválido; el
        # administrador decide (ver DELETE /api/v1/webhooks/{id}). La IP validada
        # es la que se usa para conectar (sin segunda resolución): ver docstring
        # de app.core.webhook_security.
        try:
            ip = await asyncio.to_thread(
                validate_webhook_target,
                sub["url"],
                allow_private=settings.webhook_allow_private_targets,
            )
        except ValueError as exc:
            logger.warning("webhook %s destino rechazado, no se entrega: %s", sub["url"], exc)
            return False
        try:
            url, host_headers, extensions = _pinned_request(sub["url"], ip)
            resp = await client.post(
                url,
                content=body,
                headers={**headers, **host_headers},
                extensions=extensions,
                follow_redirects=False,
            )
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
    "ciudadano",
    "metadata",
    # Ruta de dependencias de un radicado reservado.
    "from_dept",
    "to_dept",
    "assigned_to",
    # Ruta de dependencias de document.radicado.created.
    "dest_dept",
    "dest_dept_code",
    "origin_dept",
)

#: Fundamento de la reserva: NUNCA sale por un webhook, ni siquiera de un
#: radicado que se desclasifica (el evento dice POR QUÉ algo fue reservado).
_CAMPOS_DE_RESERVA = ("causal_reserva", "fundamento_juridico", "plazo_reserva_meses")

#: Claves del payload que declaran un nivel. Un evento de reclasificación
#: declara `nivel_nuevo`/`nivel_anterior` y NO `nivel_seguridad`: buscar solo
#: esta última dejaba salir el evento entero.
_CLAVES_DE_NIVEL = ("nivel_seguridad", "nivel_nuevo", "nivel_anterior")

#: Streams cuyos eventos se refieren a un radicado y por tanto DEBEN declarar
#: nivel; si no lo hacen, se tratan como clasificados (fail-closed por forma).
_PREFIJOS_DE_RADICADO = ("document.", "workflow.")

_AVISO_RECORTE = (
    "Contenido omitido: el objeto de este evento está clasificado y su texto no "
    "sale a un destino externo. Consúltelo en el sistema."
)


def _nivel_efectivo(payload: dict) -> int | None:
    """Máximo de los niveles declarados (`bool` no cuenta: es subclase de `int`)."""
    niveles = [
        v
        for k in _CLAVES_DE_NIVEL
        if isinstance(v := payload.get(k), int) and not isinstance(v, bool)
    ]
    return max(niveles) if niveles else None


def recortar_si_clasificado(envelope: dict) -> dict:
    """Devuelve el sobre sin contenido si el objeto está clasificado.

    - La causal, el fundamento y el plazo de reserva se quitan SIEMPRE.
    - El nivel efectivo es el MÁXIMO de `nivel_seguridad`, `nivel_nuevo` y
      `nivel_anterior`: reclasificar un reservado a público no puede filtrar lo
      que dejó de ser.
    - Un evento de `document.*`/`workflow.*` que no declara ninguno sale
      mínimo (tipo, id, fechas): sin tracking ni dependencias ni `dedup_key`.
    """
    payload = envelope.get("payload")
    if not isinstance(payload, dict):
        return envelope

    sin_reserva = {k: v for k, v in payload.items() if k not in _CAMPOS_DE_RESERVA}
    nivel = _nivel_efectivo(payload)

    if nivel is None:
        tipo = envelope.get("event_type")
        if isinstance(tipo, str) and tipo.startswith(_PREFIJOS_DE_RADICADO):
            minimo = {
                k: envelope[k]
                for k in ("event_id", "event_type", "version", "tenant_slug", "occurred_at")
                if k in envelope
            }
            ident = payload.get("id") or payload.get("radicado_id")
            return {
                **minimo,
                "payload": {**({"id": ident} if ident else {}), "_recorte": _AVISO_RECORTE},
            }
        return {**envelope, "payload": sin_reserva}

    if nivel < _NIVEL_RESERVADA:
        return {**envelope, "payload": sin_reserva}

    recortado = {k: v for k, v in sin_reserva.items() if k not in _CAMPOS_CON_CONTENIDO}
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
        async with httpx.AsyncClient(timeout=_TIMEOUT, follow_redirects=False) as client:
            for sub in subs:
                await _deliver_one(client, sub, body, tenant_slug)
        return len(subs)
    except Exception as exc:  # noqa: BLE001 — best-effort: no romper el consumidor
        logger.warning("dispatch de webhooks falló para %s: %s", tenant_slug, exc)
        return 0
