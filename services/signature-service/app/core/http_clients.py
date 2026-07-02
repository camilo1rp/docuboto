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

    RIESGO RESIDUAL ACEPTADO (fuera del alcance de este incremento, Q3 —
    no tocar notification-service): dentro de signature-service el OTP en
    claro NUNCA se persiste/loguea/audita (ver app/core/otp.py), pero una vez
    que este payload cruza la red hacia notification-service, ese servicio
    podría loguear/persistir el ``body`` de las notificaciones que procesa
    como parte de su propio pipeline genérico — algo que este servicio no
    controla ni puede verificar desde aquí. Requiere confirmación por
    separado en notification-service (o enmascarar/excluir este tráfico de
    su logging) antes de considerar el invariante "OTP nunca persistido"
    end-to-end.
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
