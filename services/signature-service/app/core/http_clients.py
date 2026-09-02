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
from base64 import b64encode

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


class SigningKeyStatusUnavailableError(RuntimeError):
    """auth-service no respondió (o respondió con error de infraestructura)
    al consultar ``GET /internal/signing-key/status``. FAIL-CLOSED: el
    llamante (``CadenaFirmaService.firmar``) NO puede simplemente asumir "sin
    cert activo" ante un fallo de red — eso confundiría "el usuario no tiene
    firma personal" con "auth-service está caído" y degradaría en silencio a
    HMAC nativo cuando el actor SÍ tiene un cert activo. Se traduce en un 503
    ``canal_firma_personal_no_disponible``."""


class SignDigestRemoteUnavailableError(RuntimeError):
    """Fallo de red/timeout/5xx al llamar ``POST /internal/signing-key/sign-digest``
    (auth-service) — FAIL-CLOSED, nunca cae en silencio a otro proveedor. El
    llamante lo traduce en 503 ``canal_firma_personal_no_disponible``."""


class SigningKeyRevocationStatusUnavailableError(RuntimeError):
    """auth-service no respondió (o respondió con error de infraestructura)
    al consultar ``GET /internal/signing-key/revocation-status`` (E17/F4,
    Incremento 2, A2). El llamante (``app.routers.signature.verify``) NO
    puede asumir "vigente" ante este fallo — ver
    ``settings.verify_personal_revocation_require``: por defecto (fail-closed)
    se traduce en ``valida=False``/``motivo="revocacion_no_verificable"``
    (sin difamar la cripto: ``firma_criptograficamente_valida`` se mantiene
    aparte)."""


class SignDigestRemoteError(RuntimeError):
    """auth-service respondió con un error de NEGOCIO (422 sin cert activo,
    401 TOTP incorrecto, 423 TOTP bloqueado) a ``sign-digest`` — a diferencia
    de :class:`SignDigestRemoteUnavailableError`, esto es una respuesta
    válida que el llamante debe mapear a su propio código HTTP (mismo
    criterio que ``verify_totp``/``_gate_2fa_totp`` para el 2FA por TOTP)."""

    def __init__(self, status_code: int, detail: dict) -> None:
        self.status_code = status_code
        self.detail = detail
        super().__init__(f"auth-service sign-digest -> HTTP {status_code}: {detail}")


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
            # D-02: `/send` es un endpoint servicio-a-servicio sellado; el gateway
            # nunca reenvía X-Internal-Token (whitelist de FORWARDED_REQUEST_HEADERS),
            # así que solo un llamante interno con el secreto compartido lo alcanza.
            resp = await client.post(
                "/api/v1/notifications/send",
                json=payload,
                headers={"X-Internal-Token": settings.notification_internal_token},
            )
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


async def get_signing_key_status_remote(authorization: str | None) -> dict:
    """Consulta ``GET /internal/signing-key/status`` (auth-service) para el
    firmante autenticado — usado por ``CadenaFirmaService.firmar()`` para
    decidir entre el camino ``xades_personal`` (``estado == "activo"``) y el
    fallback nativo HMAC (E17/F4, Incremento 1).

    Reenvía el ``Authorization`` TAL CUAL (D1 — auth-service deriva la
    identidad del propio bearer, nunca de un parámetro que este servicio
    pudiera mandar) MÁS ``X-Internal-Token`` (demuestra que el llamador es
    signature-service — doble gate, mismo patrón que ``sign_digest_remote``).

    FAIL-CLOSED: cualquier fallo de red/timeout/HTTP inesperado se traduce
    en :class:`SigningKeyStatusUnavailableError` — nunca se interpreta un
    fallo de infraestructura como "sin cert activo" (ver la nota de esa
    excepción).
    """
    if not authorization:
        raise SigningKeyStatusUnavailableError("Authorization header missing")
    headers = {
        "Authorization": authorization,
        "X-Internal-Token": settings.signing_key_internal_token,
    }
    try:
        async with httpx.AsyncClient(
            base_url=settings.auth_service_url, timeout=settings.signing_key_sign_timeout_seconds
        ) as client:
            resp = await client.get("/internal/signing-key/status", headers=headers)
    except httpx.RequestError as exc:
        raise SigningKeyStatusUnavailableError(f"auth-service inalcanzable: {exc}") from exc

    if resp.status_code != 200:
        raise SigningKeyStatusUnavailableError(
            f"auth-service → HTTP {resp.status_code} al consultar signing-key/status: "
            f"{resp.text[:200]}"
        )
    return resp.json()


async def sign_digest_remote(
    authorization: str | None, digest: bytes, totp_code: str
) -> dict:
    """Firma remota (E17/F4, Incremento 1) — ``POST
    /internal/signing-key/sign-digest`` (auth-service). ``digest`` es el
    SHA-256 de ``C14N-exclusiva(ds:SignedInfo)`` (ver
    ``app.providers.local.LocalSigner.sign``, técnica validada en el Paso 0
    del incremento) — NUNCA el documento completo, y la clave privada del
    usuario NUNCA transita por signature-service ni por esta función: solo
    el digest de ida y la firma cruda de vuelta.

    Doble gate saliente: ``Authorization`` reenviado TAL CUAL (D1 — identidad
    SIEMPRE del bearer del firmante, nunca de un ``user_id`` que este
    servicio pudiera enviar, cierra IDOR) MÁS ``X-Internal-Token``.

    ``totp_code`` viaja en el body — auth-service lo verifica-y-consume
    atómicamente JUNTO con la firma, en su PROPIA transacción (D1 del
    contrato de firma personal): a diferencia del 2FA por TOTP del
    OTP-correo/TOTP nativo de este servicio (``_gate_2fa_totp``), aquí NO hay
    un paso de verificación separado en signature-service — el factor y el
    uso de la clave se unen del lado de auth-service.

    Devuelve el body JSON de auth-service en éxito (``signature_value_b64``,
    ``certificado_pem``, ``certificado_fingerprint``, ``serial``,
    ``not_after``). Lanza :class:`SignDigestRemoteUnavailableError`
    (fail-closed, red/timeout/5xx) o :class:`SignDigestRemoteError`
    (respuesta de negocio 4xx — el llamante la mapea a su propio código,
    mismo patrón que ``_gate_2fa_totp`` con ``verify_totp``).
    """
    if not authorization:
        raise SignDigestRemoteUnavailableError("Authorization header missing")
    headers = {
        "Authorization": authorization,
        "X-Internal-Token": settings.signing_key_internal_token,
    }
    body = {
        "digest_b64": b64encode(digest).decode("ascii"),
        "alg": "RSA-SHA256",
        "prehashed": True,
        "totp_code": totp_code,
    }
    try:
        async with httpx.AsyncClient(
            base_url=settings.auth_service_url, timeout=settings.signing_key_sign_timeout_seconds
        ) as client:
            resp = await client.post(
                "/internal/signing-key/sign-digest", json=body, headers=headers
            )
    except httpx.RequestError as exc:
        raise SignDigestRemoteUnavailableError(f"auth-service inalcanzable: {exc}") from exc

    if resp.status_code == 200:
        return resp.json()
    if resp.status_code >= 500:
        raise SignDigestRemoteUnavailableError(
            f"auth-service → HTTP {resp.status_code} al firmar el digest: {resp.text[:200]}"
        )
    try:
        detail = resp.json().get("detail", {})
    except Exception:  # noqa: BLE001 — cuerpo no-JSON, fail-closed con detalle vacío
        detail = {}
    if not isinstance(detail, dict):
        detail = {"detail": detail}
    raise SignDigestRemoteError(resp.status_code, detail)


async def get_signing_key_revocation_status_remote(fingerprint: str, tenant_slug: str) -> dict:
    """Consulta ``GET /internal/signing-key/revocation-status`` (auth-service,
    E17/F4 Incremento 2, A2 — revocación por estado en BD + chequeo en
    línea, SIN CRL offline) para el certificado LEAF ``fingerprint`` de una
    firma ``xades_personal`` — llamado por ``POST /verify``
    (``app.routers.signature``) en CADA verificación, nunca cacheado (la
    revocación debe ser observable de inmediato).

    A diferencia de ``sign_digest_remote``/``get_signing_key_status_remote``,
    NO reenvía ``Authorization`` — ``/verify`` es una consulta pública sin
    firmante interactivo detrás; el gate es SOLO ``X-Internal-Token`` MÁS
    ``X-Tenant-Slug`` (auth-service fija el ``search_path`` con ese header:
    una huella de OTRO tenant nunca resuelve ``vigente``, fail-closed).

    Devuelve el body JSON de auth-service tal cual (``status``,
    ``revocado_at``). Lanza :class:`SigningKeyRevocationStatusUnavailableError`
    (fail-closed, red/timeout/HTTP inesperado) — el llamante decide cómo
    degradar según ``settings.verify_personal_revocation_require``.
    """
    headers = {
        # El de DOCUMENT-service, no el de auth: cada servicio valida el suyo.
        "X-Internal-Token": settings.document_internal_token,
        "X-Tenant-Slug": tenant_slug,
    }
    try:
        async with httpx.AsyncClient(
            base_url=settings.auth_service_url, timeout=settings.signing_key_sign_timeout_seconds
        ) as client:
            resp = await client.get(
                "/internal/signing-key/revocation-status",
                params={"certificado_fingerprint": fingerprint},
                headers=headers,
            )
    except httpx.RequestError as exc:
        raise SigningKeyRevocationStatusUnavailableError(
            f"auth-service inalcanzable: {exc}"
        ) from exc

    if resp.status_code != 200:
        raise SigningKeyRevocationStatusUnavailableError(
            f"auth-service → HTTP {resp.status_code} al consultar "
            f"revocation-status: {resp.text[:200]}"
        )
    return resp.json()


# ── Huella vigente de un borrador (E23 Inc.4, RF-PRO-11) ────────────────────


class HuellaNoVerificableError(Exception):
    """No se pudo comprobar si el documento sigue siendo el mismo.

    **Fail-closed, y esta es la decisión de fondo del guardián**: no poder
    comprobar NO es lo mismo que «no ha cambiado». Si un fallo de red se
    interpretara como «sigue igual», bastaría con que document-service estuviera
    caído para que se pudiera firmar sobre un texto ya modificado — y ese es el
    momento en que menos se querría poder.
    """


class BorradorDesaparecidoError(HuellaNoVerificableError):
    """El borrador ya no existe. Subclase a propósito: el efecto es el mismo
    (denegar), pero el mensaje puede ser más útil que «no se pudo comprobar»."""


async def get_huella_borrador(borrador_id: str, tenant_slug: str) -> dict:
    """Huella VIGENTE del cuerpo y del documento de un borrador.

    La mitad síncrona de la invalidación. La otra es un evento, y un consumidor
    de eventos puede retrasarse: si la garantía viviera solo ahí, existiría una
    ventana en la que alguien resuelve su turno sobre el texto viejo.

    Sellado con `X-Internal-Token` y SIN reenviar el `Authorization` del
    firmante: la respuesta es content-free (dos huellas, una versión, un
    booleano), así que no hay nada que recortar por clearance y propagar la
    identidad solo ampliaría la superficie.
    """
    headers = {
        "X-Internal-Token": settings.signing_key_internal_token,
        "X-Tenant-Slug": tenant_slug,
    }
    try:
        async with httpx.AsyncClient(
            base_url=settings.document_service_url, timeout=10.0
        ) as client:
            resp = await client.get(
                f"/api/v1/internal/documentos/borradores/{borrador_id}/huella",
                headers=headers,
            )
    except httpx.RequestError as exc:
        raise HuellaNoVerificableError(f"document-service inalcanzable: {exc}") from exc

    if resp.status_code == 404:
        raise BorradorDesaparecidoError(f"el borrador {borrador_id} ya no existe")
    if resp.status_code != 200:
        raise HuellaNoVerificableError(
            f"document-service -> HTTP {resp.status_code} al consultar la huella: "
            f"{resp.text[:200]}"
        )
    try:
        datos = resp.json()
    except ValueError as exc:
        raise HuellaNoVerificableError(f"respuesta ilegible: {exc}") from exc
    if not isinstance(datos, dict):
        raise HuellaNoVerificableError("respuesta ilegible: no es un objeto")
    return datos
