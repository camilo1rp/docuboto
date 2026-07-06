# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Clientes HTTP directos servicio-a-servicio para el 2FA por OTP-correo
(E06 Inc.2, RF-FIR-13).

Mismo patrón que ``app/clients/storage_client.py`` (Inc.1): llamadas
DIRECTAS a ``notification-service``/``auth-service``, NO a través del
api-gateway (decisión del usuario, Q3).

Ambas funciones son usadas EXCLUSIVAMENTE por
``CadenaFirmaService.emitir_challenge`` con criterio FAIL-CLOSED: cualquier
excepción aquí se traduce en un 503 ``canal_2fa_no_disponible`` que aborta
por completo la emisión del challenge (rollback de la fila ya insertada,
gracias a que la llamada ocurre dentro de la misma transacción) — nunca
queda un challenge huérfano cuyo OTP jamás llegó al firmante.
"""

from __future__ import annotations

import uuid

import httpx

from app.core.config import settings


class SignerEmailError(RuntimeError):
    """auth-service no resolvió el email del firmante autenticado."""


class OtpEmailDeliveryError(RuntimeError):
    """notification-service no aceptó el envío del correo con el código OTP."""


class TotpVerifyUnavailableError(RuntimeError):
    """auth-service no respondió (o respondió con error) al verificar un TOTP.

    FAIL-CLOSED (mismo criterio que ``SignerEmailError``/``OtpEmailDeliveryError``):
    el llamante (``CadenaFirmaService._gate_2fa``) traduce esto en un 503
    ``canal_2fa_no_disponible`` que aborta la firma.
    """


async def get_signer_email(authorization: str | None) -> str:
    """Resuelve el email del firmante autenticado vía ``auth-service GET /me``.

    Reenvía el header ``Authorization`` tal cual llegó a este servicio — el
    firmante ya fue autenticado por ``auth-service /validate`` (el gateway
    inyectó ``X-User-Id`` a partir de ese mismo bearer); ``/me`` simplemente
    revalida el token y expone ``email``.

    ASUNCIÓN EXPLÍCITA: este servicio confía en que ``X-User-Id`` (el actor,
    usado en TODO el resto de ``emitir_challenge``) y el ``sub`` del bearer
    reenviado aquí (de donde ``/me`` saca el email) identifican al MISMO
    usuario — garantía que provee el api-gateway al derivar ambos del mismo
    token en la misma request. Es fail-safe si esa garantía se rompiera (el
    OTP llegaría al dueño del bearer, no al firmante, quien simplemente no
    podría completar la firma), pero es una divergencia de fuente de
    identidad real. Endurecimiento posible (no implementado aquí, fuera del
    alcance de este incremento): que ``/me`` valide que su ``sub`` coincide
    con ``X-User-Id`` antes de devolver el email.
    """
    if not authorization:
        raise SignerEmailError("Authorization header missing")
    headers = {"Authorization": authorization}
    try:
        async with httpx.AsyncClient(
            base_url=settings.auth_service_url, timeout=5.0
        ) as client:
            resp = await client.get("/api/v1/auth/me", headers=headers)
    except httpx.RequestError as exc:
        raise SignerEmailError(f"auth-service inalcanzable: {exc}") from exc

    if resp.status_code != 200:
        raise SignerEmailError(
            f"auth-service → HTTP {resp.status_code} al resolver /me: {resp.text[:200]}"
        )
    email = resp.json().get("email")
    if not email:
        raise SignerEmailError("auth-service /me no devolvió email")
    return email


async def send_otp_email(
    *,
    recipient_email: str,
    tenant_slug: str,
    objeto_tipo: str,
    solicitud_id: uuid.UUID,
    otp: str,
    ttl_seconds: int,
) -> None:
    """Envía el código OTP por correo vía ``notification-service POST /send``.

    El cuerpo del correo lleva SOLO el identificador del acto (objeto_tipo +
    un prefijo corto del solicitud_id) y el código — NUNCA el
    ``payload_sha256``/``hash_documento`` del documento que se firma.

    RIESGO RESIDUAL — CERRADO (seguimiento de seguridad de E06 Inc.2,
    RF-FIR-13): el body con el OTP en claro sigue viajando por SMTP (es
    inherente al 2FA por correo) y notification-service sigue viéndolo para
    poder enviarlo, pero ya no lo persiste — ``payload["sensible"] = True``
    hace que notification-service envíe el body real y solo guarde en su
    tabla ``notifications`` un placeholder redactado (ver notification-service
    ``app/services/notification_service.py::REDACTED_BODY`` y su migración
    ``008_notification_sensible.sql``). Con esto el invariante "el OTP en
    claro NUNCA se persiste" (dentro de signature-service, ver
    ``app/core/otp.py``) se sostiene también en notification-service, de
    punta a punta.
    """
    solicitud_corta = str(solicitud_id).split("-")[0]
    minutos = max(ttl_seconds // 60, 1)
    subject = "Código de verificación para su firma electrónica"
    body = (
        f"Se solicitó un código de verificación para firmar el acto "
        f"{objeto_tipo}:{solicitud_corta}.\n\n"
        f"Código: {otp}\n\n"
        f"Válido por {minutos} minutos. Si usted no solicitó esta firma, "
        "ignore este mensaje."
    )
    payload = {
        "recipient_email": recipient_email,
        "subject": subject,
        "body": body,
        "tenant_slug": tenant_slug,
        # RF-FIR-13: el body lleva el OTP en claro — notification-service lo
        # envía por SMTP igual, pero NO lo persiste (ver docstring arriba).
        "sensible": True,
    }
    try:
        async with httpx.AsyncClient(
            base_url=settings.notification_service_url, timeout=5.0
        ) as client:
            resp = await client.post("/api/v1/notifications/send", json=payload)
    except httpx.RequestError as exc:
        raise OtpEmailDeliveryError(f"notification-service inalcanzable: {exc}") from exc

    if resp.status_code not in (200, 201, 202):
        raise OtpEmailDeliveryError(
            f"notification-service → HTTP {resp.status_code} al enviar OTP: "
            f"{resp.text[:200]}"
        )


async def verify_totp(
    authorization: str | None, code: str, *, solicitud_id: uuid.UUID | None = None
) -> dict:
    """Verifica-y-consume un código TOTP vía ``auth-service POST /internal/totp/verify``.

    Reenvía el header ``Authorization`` TAL CUAL llegó a este servicio (D1 del
    contrato TOTP): auth-service deriva la identidad del propio bearer
    (``sub`` -> ``auth_users.id``), NUNCA de un ``user_id`` que este servicio
    pudiera mandar en el body — cierra IDOR (el firmante solo puede verificar
    SU PROPIO TOTP).

    ``solicitud_id`` se envía como ``contexto`` (campo ya soportado por
    ``InternalTotpVerifyRequest``): un identificador de correlación, NUNCA un
    secreto, que permite atar el audit ``totp.verificado`` de auth-service al
    ``firma.turno_firmado`` concreto de este servicio (corroboración
    independiente del factor verificado — DUR 1074/2015 art.2.2.2.47.4,
    Ac.001/2024 trazabilidad del acto). Opcional: su ausencia no degrada el
    fail-closed existente.

    Timeout agresivo (``settings.totp_verify_timeout_seconds``, ~3s) y
    fail-closed: cualquier fallo de red o HTTP != 200 se traduce en
    ``TotpVerifyUnavailableError`` — el llamante la convierte en 503
    ``canal_2fa_no_disponible`` (nunca cae en silencio a otro factor).

    Devuelve el body JSON de auth-service tal cual (``valido``, y en caso de
    fallo ``motivo``/``intentos_restantes``/``locked_until``; en éxito
    ``timestep``/``factor_tipo``) — el mapeo a HTTPException vive en
    ``CadenaFirmaService._gate_2fa``, no aquí.
    """
    if not authorization:
        raise TotpVerifyUnavailableError("Authorization header missing")
    headers = {"Authorization": authorization}
    body: dict = {"code": code}
    if solicitud_id is not None:
        body["contexto"] = str(solicitud_id)
    try:
        async with httpx.AsyncClient(
            base_url=settings.auth_service_url, timeout=settings.totp_verify_timeout_seconds
        ) as client:
            resp = await client.post("/internal/totp/verify", json=body, headers=headers)
    except httpx.RequestError as exc:
        raise TotpVerifyUnavailableError(f"auth-service inalcanzable: {exc}") from exc

    if resp.status_code != 200:
        raise TotpVerifyUnavailableError(
            f"auth-service → HTTP {resp.status_code} al verificar TOTP: {resp.text[:200]}"
        )
    return resp.json()
