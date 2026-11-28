# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Antirrobots de la ventanilla pública: verificar el reto ALTCHA y CONSUMIRLO (C16).

El cálculo vive en `document/core/altcha.py` (puro). Aquí está lo que tiene estado: el
antirreplay, en la tabla `altcha_usados` del schema de la entidad (ADR-049). Un reto
resuelto sirve UNA vez: sin esto, quien resuelve uno podría radicar con él las veces que
quiera hasta que venza.

Fail-CLOSED en todo: sin clave configurada, o sin poder escribir el consumo, no se
radica (503 `antirrobots_no_disponible`, que nombra los canales alternos). No se radica
sin poder garantizar un solo uso; y no es un «cierre total no neutral»: no corre
plazo alguno sin radicado, y el canal presencial/correo existe.

El consumo va en la conexión de la petición: si la radicación revierte, el reto vuelve a
estar libre, y dos peticiones simultáneas con el mismo reto se ordenan en la clave
primaria — la segunda espera a la primera y, si confirmó, recibe `antirrobots_usado`.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import HTTPException, status

from docuboto.config import document as settings
from document.core import altcha

logger = logging.getLogger(__name__)

_DETALLES = {
    "antirrobots_invalido": (
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "La verificación automática no es válida. Recargue la página y vuelva a intentarlo.",
    ),
    "antirrobots_vencido": (
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "La verificación automática venció. Se pedirá una nueva: vuelva a enviar el formulario.",
    ),
    "antirrobots_usado": (
        status.HTTP_409_CONFLICT,
        "Esa verificación ya se usó. Se pedirá una nueva: vuelva a enviar el formulario.",
    ),
    "antirrobots_no_disponible": (
        status.HTTP_503_SERVICE_UNAVAILABLE,
        "No se pudo comprobar la verificación automática. No se radicó nada. Presente su "
        "documento en la ventanilla de la entidad o por el correo institucional.",
    ),
}


def error(codigo: str, contacto: dict[str, str] | None = None) -> HTTPException:
    """El error de antirrobots. `contacto` (solo en `antirrobots_no_disponible`) es el
    contacto de radicación REAL de la entidad, leído de sus parámetros: un 503 que dice
    «presente su documento en la ventanilla» sin decir dónde no es un canal alterno."""
    codigo_http, detalle = _DETALLES[codigo]
    cuerpo: dict[str, object] = {"error": codigo, "detail": detalle, "status": codigo_http}
    if contacto:
        cuerpo["detail"] = (
            detalle + " Contacto de la entidad: "
            + "; ".join(f"{k}: {v}" for k, v in contacto.items()) + "."
        )
        cuerpo["contacto"] = contacto
    return HTTPException(status_code=codigo_http, detail=cuerpo)


def disponible() -> bool:
    """¿Podría radicarse AHORA? Hay una clave HMAC de >= 32 bytes.

    Es lo que `GET /ventanilla/estado` publica como `antirrobots_disponible`: sin esto
    el estado decía «habilitada» con una ventanilla que respondería 503 a todos. El
    almacén del antirreplay es la misma base que atiende la consulta del estado.
    """
    return len(settings.altcha_hmac_key.encode()) >= altcha.HMAC_MIN_BYTES


def clave_configurada() -> bytes:
    """La clave HMAC del despliegue, o 503: sin ella no hay forma de autenticar un reto."""
    clave = settings.altcha_hmac_key.encode()
    if len(clave) < altcha.HMAC_MIN_BYTES:
        logger.error("ALTCHA_HMAC_KEY ausente o demasiado corta: no se puede verificar")
        raise error("antirrobots_no_disponible")
    return clave


def emitir(tenant: str, *, ahora: int | None = None) -> dict[str, Any]:
    return altcha.emitir_reto(
        clave=clave_configurada(),
        tenant=tenant,
        ahora=int(time.time()) if ahora is None else ahora,
        ttl=settings.altcha_ttl_segundos,
        maxnumber=settings.altcha_maxnumber,
    )


async def exigir_reto(
    conn: Any, payload: str, tenant: str, *, ahora: int | None = None
) -> None:
    """Verifica el payload y consume el reto, o lanza el HTTPException que corresponde."""
    instante = int(time.time()) if ahora is None else ahora
    try:
        challenge, expires = altcha.verificar(
            payload,
            clave=clave_configurada(),
            tenant=tenant,
            maxnumber=settings.altcha_maxnumber,
            ahora=instante,
        )
    except altcha.AltchaRechazado as exc:
        raise error(exc.codigo) from None
    # Se consume tras validar firma y vencimiento: no se escriben filas por payloads basura.
    # La vida cubre lo que le queda al reto más la holgura de reloj.
    ttl = max(1, expires - instante + settings.altcha_skew_segundos)
    try:
        await conn.execute("DELETE FROM altcha_usados WHERE expira_en < now()")
        nuevo = await conn.fetchval(
            "INSERT INTO altcha_usados (challenge, expira_en) "
            "VALUES ($1, now() + make_interval(secs => $2::int)) "
            "ON CONFLICT (challenge) DO NOTHING RETURNING challenge",
            challenge,
            ttl,
        )
    except Exception:  # noqa: BLE001 — sin poder anotar el consumo no hay «un solo uso»
        logger.warning("antirreplay ALTCHA: no se pudo anotar el consumo", exc_info=True)
        raise error("antirrobots_no_disponible") from None
    if nuevo is None:
        raise error("antirrobots_usado")
