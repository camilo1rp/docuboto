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

``get_signer_identity`` y ``send_otp_email`` son usadas por
``CadenaFirmaService.emitir_challenge`` con criterio FAIL-CLOSED: cualquier
excepción aquí se traduce en un 503 ``canal_2fa_no_disponible`` que aborta
por completo la emisión del challenge (rollback de la fila ya insertada,
gracias a que la llamada ocurre dentro de la misma transacción) — nunca
queda un challenge huérfano cuyo OTP jamás llegó al firmante.
"""

from __future__ import annotations

import logging
import uuid
from base64 import b64encode
from dataclasses import dataclass
from urllib.parse import quote

import httpx

from app.core.config import settings

logger = logging.getLogger("signature.http_clients")


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


class TotpStatusUnavailableError(RuntimeError):
    """auth-service no respondió (o respondió con error) a ``GET /internal/totp/status``.

    FAIL-CLOSED (AREA-04-02): no poder saber si el actor tiene TOTP no es lo
    mismo que «no lo tiene». Si se leyera como «no lo tiene», bastaría con tumbar
    auth-service para bajar el segundo factor del acto al correo. El llamante lo
    traduce en 503 ``canal_2fa_no_disponible``.
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


@dataclass(frozen=True, slots=True)
class SignerIdentity:
    """Lo que auth-service ``/me`` dice del DUEÑO del bearer reenviado."""

    email: str
    #: ``auth_users.id`` del dueño del bearer (cuando ``/me`` recibe
    #: ``X-Tenant-Slug`` y el usuario está aprovisionado; si no, el ``sub`` de
    #: Keycloak, que nunca coincide con un actor — fail-closed por construcción).
    user_id: str


async def get_signer_identity(
    authorization: str | None, tenant_slug: str
) -> SignerIdentity:
    """Resuelve email e identidad del firmante vía ``auth-service GET /me``.

    Reenvía el ``Authorization`` TAL CUAL llegó (``/me`` revalida el token) y
    añade ``X-Tenant-Slug``: sin él ``/me`` no tiene contexto de tenant y
    devuelve el ``sub`` de Keycloak en lugar de ``auth_users.id``, que es el
    que viaja como ``X-User-Id``.

    **Devolver ``user_id`` es lo que cierra AREA-04-05.** Este servicio
    atribuye el acto a ``X-User-Id`` (el actor) pero el OTP sale al correo del
    BEARER. Antes se asumía que los dos eran la misma persona porque el gateway
    los deriva del mismo token, y el docstring lo llamaba «fail-safe»: era al
    revés — quien tiene el bearer recibe el código y completa él mismo una
    firma atribuida a otro. El gateway lo impide desde fuera, no desde un
    servicio interno comprometido que firma ``X-User-Id=<víctima>`` con su
    propio bearer. El llamante compara ``user_id`` con el actor ANTES de crear
    el desafío.
    """
    if not authorization:
        raise SignerEmailError("Authorization header missing")
    headers = {"Authorization": authorization, "X-Tenant-Slug": tenant_slug}
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
    body = resp.json()
    email = body.get("email")
    if not email:
        raise SignerEmailError("auth-service /me no devolvió email")
    user_id = body.get("user_id")
    if not user_id:
        raise SignerEmailError("auth-service /me no devolvió user_id")
    return SignerIdentity(email=email, user_id=str(user_id))


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
    headers = {
        "Authorization": authorization,
        "X-Internal-Token": settings.signing_key_internal_token,
    }
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


async def get_totp_status_remote(authorization: str | None) -> dict:
    """Consulta ``GET /internal/totp/status`` (auth-service): ¿el dueño del
    bearer tiene un TOTP ACTIVO? (AREA-04-02). Devuelve ``{"activo": bool,
    "user_id": str | None}``.

    Mismo doble gate que ``get_signing_key_status_remote`` (``Authorization``
    TAL CUAL + ``X-Internal-Token``) y mismo timeout corto. FAIL-CLOSED: red,
    timeout o HTTP != 200 -> :class:`TotpStatusUnavailableError`.
    """
    if not authorization:
        raise TotpStatusUnavailableError("Authorization header missing")
    headers = {
        "Authorization": authorization,
        "X-Internal-Token": settings.signing_key_internal_token,
    }
    try:
        async with httpx.AsyncClient(
            base_url=settings.auth_service_url, timeout=settings.totp_verify_timeout_seconds
        ) as client:
            resp = await client.get("/internal/totp/status", headers=headers)
    except httpx.RequestError as exc:
        raise TotpStatusUnavailableError(f"auth-service inalcanzable: {exc}") from exc

    if resp.status_code != 200:
        raise TotpStatusUnavailableError(
            f"auth-service → HTTP {resp.status_code} al consultar totp/status: "
            f"{resp.text[:200]}"
        )
    try:
        body = resp.json()
    except ValueError as exc:
        raise TotpStatusUnavailableError(f"respuesta ilegible: {exc}") from exc
    if not isinstance(body, dict) or "activo" not in body:
        raise TotpStatusUnavailableError("respuesta ilegible: falta «activo»")
    return body


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
        # Habla con AUTH-service, así que va el suyo. Aquí se coló el token de
        # document-service al corregir el de la huella: el arreglo entró en la
        # función equivocada y rompió la comprobación de revocación, que estaba
        # sana. Cada llamada lleva el token del servicio AL QUE VA.
        "X-Internal-Token": settings.signing_key_internal_token,
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


class ObjetoDesaparecidoError(HuellaNoVerificableError):
    """El objeto de la cadena ya no existe (404 del productor)."""


class BorradorDesaparecidoError(ObjetoDesaparecidoError):
    """El borrador ya no existe. Subclase a propósito: el efecto es el mismo
    (denegar), pero el mensaje puede ser más útil que «no se pudo comprobar»."""


class HuellaSinContenidoError(HuellaNoVerificableError):
    """El productor existe y responde, pero el objeto no tiene contenido que
    sellar (radicado sin PDF, expediente sin índice, anexo sin huella): 409.

    Subclase de ``HuellaNoVerificableError`` a propósito: quien no la distinga
    cae al 503 fail-closed, nunca a «sin cambios». Se distingue porque el
    mensaje útil es otro («adjunte el documento»), no «reintente»."""

    def __init__(self, codigo: str, mensaje: str) -> None:
        super().__init__(mensaje)
        self.codigo = codigo


async def _get_huella_interna(
    *, base_url: str, token: str, path: str, tenant_slug: str, productor: str
) -> dict:
    """GET sellado con ``X-Internal-Token`` del PRODUCTOR al que se pregunta.

    Fail-closed en todo lo que no sea un 200 legible. Sin ``Authorization``: la
    respuesta es content-free (una huella) y propagar la identidad ampliaría la
    superficie sin ganar nada.
    """
    headers = {"X-Internal-Token": token, "X-Tenant-Slug": tenant_slug}
    try:
        async with httpx.AsyncClient(base_url=base_url, timeout=10.0) as client:
            resp = await client.get(path, headers=headers)
    except httpx.RequestError as exc:
        # El texto de la excepción de httpx lleva el host y el puerto internos.
        logger.warning("%s inalcanzable al consultar la huella: %s", productor, exc)
        raise HuellaNoVerificableError(f"{productor} inalcanzable") from exc
    if resp.status_code == 404:
        raise ObjetoDesaparecidoError(f"el objeto no existe en {productor}")
    if resp.status_code == 409:
        try:
            error = resp.json()["detail"]["error"]
        except (ValueError, KeyError, TypeError):
            error = "objeto_sin_contenido"
        raise HuellaSinContenidoError(str(error), f"{productor}: {error}")
    if resp.status_code != 200:
        # El CUERPO del productor no se reenvía: acaba en el 503 que ve el
        # firmante y puede traer rutas, trazas o datos de otro servicio. Va al
        # log del servidor, que es donde lo necesita quien diagnostica.
        logger.warning(
            "%s -> HTTP %s al consultar la huella: %s",
            productor, resp.status_code, resp.text[:200],
        )
        raise HuellaNoVerificableError(f"{productor} respondió HTTP {resp.status_code}")
    try:
        datos = resp.json()
    except ValueError as exc:
        raise HuellaNoVerificableError(f"{productor}: respuesta ilegible") from exc
    if not isinstance(datos, dict):
        raise HuellaNoVerificableError(f"{productor}: respuesta ilegible")
    return datos


async def get_huella_radicado(radicado_ref: str, tenant_slug: str) -> dict:
    """SHA-256 del PDF vigente de un radicado (document-service, AREA-04-09).

    ``radicado_ref``: número de radicado o UUID, tal como la cadena guarda
    ``objeto_id``. Devuelve el JSON del productor (``huella_sha256``).
    """
    return await _get_huella_interna(
        base_url=settings.document_service_url,
        token=settings.document_internal_token,
        path=f"/api/v1/internal/documentos/radicados/{quote(radicado_ref, safe='')}/huella",
        tenant_slug=tenant_slug,
        productor="document-service",
    )


async def get_huella_indice_expediente(expediente_ref: str, tenant_slug: str) -> dict:
    """SHA-256 del índice electrónico vigente de un expediente (archive-service,
    AREA-04-09). ``expediente_ref``: código o UUID."""
    return await _get_huella_interna(
        base_url=settings.archive_service_url,
        token=settings.archive_internal_token,
        path=f"/api/v1/internal/expedientes/{quote(expediente_ref, safe='')}/indice/huella",
        tenant_slug=tenant_slug,
        productor="archive-service",
    )


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
        # El de DOCUMENT-service: es a él a quien se le pregunta. Mandar el de
        # auth daba 403 en TODAS las consultas y, con ellas, 503 en abrir
        # circuito, dar visto bueno y firmar: el circuito de aprobación entero
        # quedaba inoperante — fail-closed, pero inútil.
        "X-Internal-Token": settings.document_internal_token,
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
        logger.warning("document-service inalcanzable al consultar la huella: %s", exc)
        raise HuellaNoVerificableError("document-service inalcanzable") from exc

    if resp.status_code == 404:
        raise BorradorDesaparecidoError(f"el borrador {borrador_id} ya no existe")
    if resp.status_code != 200:
        logger.warning(
            "document-service -> HTTP %s al consultar la huella: %s",
            resp.status_code, resp.text[:200],
        )
        raise HuellaNoVerificableError(f"document-service respondió HTTP {resp.status_code}")
    try:
        datos = resp.json()
    except ValueError as exc:
        raise HuellaNoVerificableError("document-service: respuesta ilegible") from exc
    if not isinstance(datos, dict):
        raise HuellaNoVerificableError("document-service: respuesta ilegible")
    return datos


async def aprobar_borrador_remote(borrador_id: str, tenant_slug: str) -> bool:
    """Avisa a document-service de que el circuito de este borrador se completó.

    **Best-effort a propósito**: devuelve `False` en cualquier fallo y no
    levanta. Puede permitírselo porque no es la garantía — la garantía es que
    `BorradorService.aprobar` (document-service) consulta el estado real del
    circuito antes de aprobar, así que si este aviso se pierde el borrador
    simplemente se aprueba con un clic más, nunca se salta el circuito.

    Lo contrario sería peor de las dos maneras: levantar aquí haría que un
    document-service caído revirtiera un visto bueno ya dado y ya auditado, y
    fiarle la garantía convertiría este aviso en una copia que se queda rancia.

    Sellado con el `X-Internal-Token` de DOCUMENT-service, que es a quien se
    llama.
    """
    headers = {
        "X-Internal-Token": settings.document_internal_token,
        "X-Tenant-Slug": tenant_slug,
    }
    try:
        async with httpx.AsyncClient(
            base_url=settings.document_service_url, timeout=10.0
        ) as client:
            resp = await client.post(
                f"/api/v1/internal/documentos/borradores/{borrador_id}/aprobar",
                headers=headers,
            )
    except httpx.RequestError:
        return False
    return resp.status_code == 200
