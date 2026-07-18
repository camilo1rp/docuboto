# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Cliente del signature-service para sellar el índice al cierre (E06 Inc.1, RT-16).

Best-effort: si el signature-service no está disponible (o rechaza la
solicitud), `sign_xml`/`verify` devuelven ``None`` y el llamador decide —
decisión del usuario: el cierre del expediente NO se bloquea, el índice queda
'pendiente_firma' y puede reintentarse vía `POST /indice/firmar`.

BUGFIX (E06 Inc.1): esta versión corrige que `sign_xml` NO enviaba
``X-User-Id`` — signature-service exige esa cabecera (``Depends(get_actor)``,
401 fail-closed) para atribuir el acto de firma (RF-FIR-11/12/14) a quien
cierra el expediente. Sin ella, /sign-xml SIEMPRE devolvía 401 y el índice
nunca llegaba a firmarse.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

import httpx
from fastapi import status

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class SignedIndice:
    """Resultado de sellar el índice electrónico con XAdES-B (E06 Inc.1).

    Espejo (subconjunto) del `FirmaResponse` de signature-service. NUNCA
    transporta material de clave — solo lo que el servicio expone
    públicamente. `acreditado` es SIEMPRE False en Inc.1 (no hay CA acreditada
    ONAC): no declarar conforme una firma cualificada (RF-FIR-15).
    """

    firma_id: uuid.UUID
    minio_key_firmado: str | None
    nivel_conformidad: str | None
    xades_level: str | None
    tipo_firma: str | None
    acreditado: bool
    certificado_fingerprint: str | None


@dataclass(slots=True)
class SignOutcome:
    """Resultado ESTRUCTURADO de un intento de sellado (residual Ac. AGN
    001/2024 art. 4.3.2.4, reconciliación) — distingue el MOTIVO del fallo
    para que el control compensatorio del cierre
    (`IndexService.firmar_indice_close`) y el job de reconciliación
    (`app/jobs/indice_reconciliation.py`) decidan si reintentar
    (`transient`) o degradar sin reintentar (`seal_absent`).

    Tipo ADITIVO — `sign_xml` (usado por `firmar_indice`/
    `firmar_indice_retry`) sigue colapsando todo fallo a `None`, SIN
    cambios: `sign_xml_outcome`/`sign_indice_internal` son funciones NUEVAS,
    no un reemplazo.

    Exactamente uno de los tres queda "poblado": `signed` (éxito),
    `seal_absent=True` (503 `signing_seal_unavailable` — sello institucional
    ausente, firmar es estructuralmente imposible ahora mismo) o
    `transient=True` (cualquier otro fallo: HTTP no-2xx, timeout, servicio
    inalcanzable — puede resolverse solo con reintentar más tarde).
    """

    signed: SignedIndice | None
    seal_absent: bool = False
    transient: bool = False


@dataclass(slots=True)
class VerifyResult:
    """Veredicto de `POST /signature/verify` (E06 Inc.1, RF-FIR-15)."""

    valida: bool
    nivel_conformidad: str | None
    acreditado: bool
    motivo: str | None
    # `revocacion_status` (E17/F4 Incremento 2, A2) -- SOLO poblado para
    # firmas `provider='xades_personal'` (`vigente`/`revocado`/
    # `no_verificable`/`desconocido`); `None` para el sello institucional o
    # HMAC nativo (no aplica, ver signature-service `VerifyResponse`).
    # ADITIVO, opcional: no rompe ningún llamante preexistente que no lo lea.
    revocacion_status: str | None = None


async def sign_xml(
    *,
    tenant_slug: str,
    actor_id: str,
    payload_xml: str,
    objeto_id: str,
    indice_id: str,
    version: int,
) -> SignedIndice | None:
    """Solicita el sellado XAdES-B del índice vigente (E06 Inc.1).

    `actor_id` (X-User-Id, UUID interno de `auth_users.id`) viaja SIEMPRE como
    cabecera — nunca en el cuerpo (RF-FIR-11/12: el firmante NO se declara,
    se autentica). `indice_id`/`version` identifican la versión del índice que
    se está firmando (fila de `expediente_indice`), distinta de `objeto_id`
    (el `expediente_id`, preservado por compatibilidad con la auditoría).

    Best-effort: devuelve ``None`` ante cualquier fallo (sello no
    aprovisionado → 503, servicio inalcanzable, etc.) sin lanzar excepción —
    el llamador (`IndexService.firmar_indice`) deja el índice en
    'pendiente_firma' y no bloquea el cierre del expediente.
    """
    body = {
        "payload_xml": payload_xml,
        "objeto_tipo": "indice",
        "objeto_id": objeto_id,
        "formato": "XAdES",
        "indice_id": indice_id,
        "version": version,
    }
    headers = {"X-Tenant-Slug": tenant_slug, "X-User-Id": actor_id}
    try:
        async with httpx.AsyncClient(
            base_url=settings.signature_service_url, timeout=10.0
        ) as client:
            resp = await client.post("/api/v1/signature/sign-xml", json=body, headers=headers)
            if resp.status_code in (200, 201):
                data = resp.json()
                return SignedIndice(
                    firma_id=uuid.UUID(data["firma_id"]),
                    minio_key_firmado=data.get("minio_key_firmado"),
                    nivel_conformidad=data.get("nivel_conformidad"),
                    xades_level=data.get("xades_level"),
                    tipo_firma=data.get("tipo_firma"),
                    acreditado=bool(data.get("acreditado", False)),
                    certificado_fingerprint=data.get("certificado_fingerprint"),
                )
            logger.warning("signature-service → HTTP %s al firmar índice", resp.status_code)
    except httpx.RequestError as exc:
        logger.warning("signature-service inalcanzable al firmar índice: %s", exc)
    return None


def _outcome_from_signed_response(data: dict) -> SignedIndice:
    """Deserializa el cuerpo 2xx de `/sign-xml`/`/internal/sign-indice` en un
    `SignedIndice` — factorizado fuera de `sign_xml`/`sign_xml_outcome`/
    `sign_indice_internal` (misma forma de respuesta, `FirmaResponse` de
    signature-service) para no triplicar el mapeo campo a campo."""
    return SignedIndice(
        firma_id=uuid.UUID(data["firma_id"]),
        minio_key_firmado=data.get("minio_key_firmado"),
        nivel_conformidad=data.get("nivel_conformidad"),
        xades_level=data.get("xades_level"),
        tipo_firma=data.get("tipo_firma"),
        acreditado=bool(data.get("acreditado", False)),
        certificado_fingerprint=data.get("certificado_fingerprint"),
    )


def _outcome_from_response(resp: httpx.Response, *, contexto: str) -> SignOutcome:
    """Traduce una respuesta HTTP de signature-service a un `SignOutcome`
    ESTRUCTURADO (residual Ac. AGN 001/2024 art. 4.3.2.4) — compartido por
    `sign_xml_outcome` (`/sign-xml`) y `sign_indice_internal`
    (`/internal/sign-indice`): mismo criterio de clasificación en ambas rutas
    (`signing_seal_unavailable` → `seal_absent`; cualquier otro no-2xx →
    `transient`)."""
    if resp.status_code in (200, 201):
        return SignOutcome(signed=_outcome_from_signed_response(resp.json()))
    if resp.status_code == status.HTTP_503_SERVICE_UNAVAILABLE:
        try:
            detail = resp.json().get("detail", {})
        except ValueError:
            detail = {}
        error = detail.get("error") if isinstance(detail, dict) else None
        if error == "signing_seal_unavailable":
            logger.warning("signature-service: sello institucional ausente (%s)", contexto)
            return SignOutcome(signed=None, seal_absent=True)
    logger.warning("signature-service → HTTP %s (%s, transient)", resp.status_code, contexto)
    return SignOutcome(signed=None, transient=True)


async def sign_xml_outcome(
    *,
    tenant_slug: str,
    actor_id: str,
    payload_xml: str,
    objeto_id: str,
    indice_id: str | None,
    version: int,
    objeto_tipo: str = "indice",
) -> SignOutcome:
    """Gemelo ESTRUCTURADO de `sign_xml` (residual Ac. AGN 001/2024 art.
    4.3.2.4) — usado por `IndexService.firmar_indice_close` (control
    compensatorio del cierre) y, desde H-H (E06/F4), por
    `TransferenciaService._sellar_acta_post_commit` (sellado síncrono
    acotado del acta de entrega tras `recibir()`): a diferencia de
    `sign_xml`, que colapsa TODO fallo a `None`, aquí se distingue
    `seal_absent` (no reintentar) de `transient` (reintentar) — ver
    `SignOutcome`.

    `objeto_tipo` (default `"indice"`, retrocompat total con el único
    llamador preexistente) selecciona el tipo de objeto sellado —
    signature-service solo produce XAdES real para el allow-set
    institucional (`indice`/`acta_transferencia`, ver
    `signature-service/app/core/constants.py::XADES_ELIGIBLE_OBJETO_TIPOS`);
    cualquier otro valor cae a HMAC 'nativa' del lado del servidor. `version`
    para el acta es `indice_version` (versión del índice PINNEADA en la tx de
    recepción, no una versión propia del acta); `indice_id=None` — el acta no
    tiene índice electrónico propio que versionar.

    Mismo endpoint/cuerpo/headers que `sign_xml` (`POST /sign-xml`,
    `X-User-Id` — sellado ATRIBUIDO al humano que cierra el expediente o
    recibe la transferencia); NO usa la ruta interna de reconciliación (ver
    `sign_indice_internal`).
    """
    body = {
        "payload_xml": payload_xml,
        "objeto_tipo": objeto_tipo,
        "objeto_id": objeto_id,
        "formato": "XAdES",
        "indice_id": indice_id,
        "version": version,
    }
    headers = {"X-Tenant-Slug": tenant_slug, "X-User-Id": actor_id}
    try:
        async with httpx.AsyncClient(
            base_url=settings.signature_service_url, timeout=10.0
        ) as client:
            resp = await client.post("/api/v1/signature/sign-xml", json=body, headers=headers)
            return _outcome_from_response(resp, contexto=f"sign-xml ({objeto_tipo})")
    except httpx.RequestError as exc:
        logger.warning(
            "signature-service inalcanzable al firmar %s (sign-xml): %s", objeto_tipo, exc
        )
        return SignOutcome(signed=None, transient=True)


async def sign_indice_internal(
    *,
    tenant_slug: str,
    payload_xml: str,
    objeto_id: str,
    indice_id: str | None,
    version: int,
    objeto_tipo: str = "indice",
) -> SignOutcome:
    """Solicita el sellado vía la ruta INTERNA de reconciliación (residual
    Ac. AGN 001/2024 art. 4.3.2.4, signature-service
    `POST /internal/sign-indice`, gate D-02) — usado EXCLUSIVAMENTE por
    `app/jobs/indice_reconciliation.py` (índice Y, desde H-H E06/F4, acta de
    transferencia).

    `objeto_tipo` (default `"indice"`, retrocompat total: el llamador
    preexistente `_reconciliar_indice` nunca lo pasa) viaja en el body —
    signature-service lo valida contra su allow-set institucional
    (`InternalSignIndiceRequest`, fail-closed 422 fuera de él). `indice_id`
    es `None` para el acta (sin índice electrónico propio que versionar).

    SIN `X-User-Id` (no hay actor humano detrás): `X-Internal-Token`
    (`settings.signature_internal_token`) en su lugar — signature-service
    atribuye el sellado a `SYSTEM_RECONCILER_ID`
    (`app/core/constants.py`, MISMO valor literal en ambos servicios).
    Timeout más generoso que `sign_xml`/`sign_xml_outcome` (15s vs 10s): el
    job corre en background, sin presión de latencia de un request HTTP de
    usuario — puede permitirse esperar un poco más antes de degradar a
    `transient` y dejar que el backoff del propio job reintente.
    """
    body = {
        "objeto_tipo": objeto_tipo,
        "payload_xml": payload_xml,
        "objeto_id": objeto_id,
        "indice_id": indice_id,
        "version": version,
    }
    headers = {
        "X-Tenant-Slug": tenant_slug,
        "X-Internal-Token": settings.signature_internal_token,
    }
    try:
        async with httpx.AsyncClient(
            base_url=settings.signature_service_url, timeout=15.0
        ) as client:
            resp = await client.post(
                "/api/v1/signature/internal/sign-indice", json=body, headers=headers
            )
            return _outcome_from_response(
                resp, contexto=f"internal/sign-indice ({objeto_tipo}, reconciliación)"
            )
    except httpx.RequestError as exc:
        logger.warning(
            "signature-service inalcanzable al reconciliar el sellado de %s: %s", objeto_tipo, exc
        )
        return SignOutcome(signed=None, transient=True)


async def verify(
    *,
    tenant_slug: str,
    firma_id: str,
    payload_xml: str,
) -> VerifyResult | None:
    """Verifica la firma XAdES-B del índice (E06 Inc.1, público en signature-service).

    IMPORTANTE — cambio de semántica: `payload_xml` debe ser el XML YA
    FIRMADO (descargado de MinIO vía `app.clients.storage_client`), NO el XML
    en claro que produce `index_builder.build_xml`. El llamador
    (`IndexService.verify`) es responsable de esa descarga.

    Best-effort: devuelve ``None`` si el signature-service no está disponible
    (el llamador no declara conforme lo que no pudo verificar, RF-FIR-15).
    """
    body = {"firma_id": firma_id, "payload_xml": payload_xml}
    headers = {"X-Tenant-Slug": tenant_slug}
    try:
        async with httpx.AsyncClient(
            base_url=settings.signature_service_url, timeout=5.0
        ) as client:
            resp = await client.post("/api/v1/signature/verify", json=body, headers=headers)
            if resp.status_code == 200:
                data = resp.json()
                return VerifyResult(
                    valida=bool(data.get("valida")),
                    nivel_conformidad=data.get("nivel_conformidad"),
                    acreditado=bool(data.get("acreditado", False)),
                    motivo=data.get("motivo"),
                    revocacion_status=data.get("revocacion_status"),
                )
            logger.warning("signature-service → HTTP %s al verificar índice", resp.status_code)
    except httpx.RequestError as exc:
        logger.warning("signature-service inalcanzable al verificar índice: %s", exc)
    return None


@dataclass(slots=True)
class SignedPersonal:
    """Espejo (subconjunto) del `FirmaResponse` de `POST /sign-personal`
    (E17/F4 Incremento 1, ``provider='xades_personal'``)."""

    firma_id: uuid.UUID
    minio_key_firmado: str | None
    xades_level: str | None
    acreditado: bool
    certificado_fingerprint: str | None
    nivel_conformidad: str | None


@dataclass(slots=True)
class SignPersonalOutcome:
    """Resultado ESTRUCTURADO de `POST /sign-personal` (E17/F4 Incremento 1).

    A diferencia del sello institucional (`SignOutcome`, best-effort — un
    fallo simplemente degrada el objeto a `pendiente_firma`), la firma
    PERSONAL es un acto INTERACTIVO: requiere el TOTP del propio usuario, así
    que NUNCA se degrada en silencio — el llamante
    (`TransferenciaService.firmar_personal`) propaga el resultado al usuario
    tal cual.

    Exactamente uno de los tres queda poblado:
    - `signed` -- éxito.
    - `rejected` -- respuesta de NEGOCIO de signature-service, `(status_code,
      detail)` reenviado VERBATIM (422 `sin_certificado_activo`, 401
      `totp_incorrecto`, 403 `firmante_no_coincide`, 409 `2fa_requerido`,
      404 `object_not_found`, ...) — mismo criterio que `SignDigestRemoteError`
      en signature-service.
    - `unavailable=True` -- canal caído (red/timeout/5xx) — fail-closed 503.
    """

    signed: SignedPersonal | None
    rejected: tuple[int, dict] | None = None
    unavailable: bool = False


async def sign_personal(
    *,
    tenant_slug: str,
    authorization: str | None,
    actor_id: str,
    payload_xml: str,
    objeto_tipo: str,
    objeto_id: str,
    totp_code: str,
    firmante_id: str | None = None,
) -> SignPersonalOutcome:
    """Solicita la firma PERSONAL PKI por-usuario de `payload_xml` (E17/F4
    Incremento 1, `POST /signature/sign-personal`) — primer consumidor: el
    acta de entrega de transferencia (`TransferenciaService.firmar_personal`).

    Reenvía el `Authorization` bearer TAL CUAL (D1 — signature-service lo
    necesita para verificar-y-consumir el TOTP en auth-service; sin él la
    llamada falla 401/503 del lado de signature-service, nunca se sustituye
    por `actor_id`). `firmante_id` (opcional, defensivo): el propio
    `actor.id` del receptor — signature-service lo corrobora contra el actor
    derivado del bearer (403 `firmante_no_coincide` si no coincide).
    """
    body: dict = {
        "payload_xml": payload_xml,
        "objeto_tipo": objeto_tipo,
        "objeto_id": objeto_id,
        "totp_code": totp_code,
    }
    if firmante_id is not None:
        body["firmante_id"] = firmante_id
    headers = {"X-Tenant-Slug": tenant_slug, "X-User-Id": actor_id}
    if authorization:
        headers["Authorization"] = authorization
    try:
        async with httpx.AsyncClient(
            base_url=settings.signature_service_url, timeout=15.0
        ) as client:
            resp = await client.post(
                "/api/v1/signature/sign-personal", json=body, headers=headers
            )
    except httpx.RequestError as exc:
        logger.warning(
            "signature-service inalcanzable al firmar personalmente (%s): %s", objeto_tipo, exc
        )
        return SignPersonalOutcome(signed=None, unavailable=True)

    if resp.status_code in (200, 201):
        data = resp.json()
        return SignPersonalOutcome(
            signed=SignedPersonal(
                firma_id=uuid.UUID(data["firma_id"]),
                minio_key_firmado=data.get("minio_key_firmado"),
                xades_level=data.get("xades_level"),
                acreditado=bool(data.get("acreditado", False)),
                certificado_fingerprint=data.get("certificado_fingerprint"),
                nivel_conformidad=data.get("nivel_conformidad"),
            )
        )
    if resp.status_code >= 500:
        logger.warning(
            "signature-service → HTTP %s al firmar personalmente (%s)", resp.status_code, objeto_tipo
        )
        return SignPersonalOutcome(signed=None, unavailable=True)
    try:
        detail = resp.json().get("detail", {})
    except ValueError:
        detail = {}
    if not isinstance(detail, dict):
        detail = {"detail": detail}
    return SignPersonalOutcome(signed=None, rejected=(resp.status_code, detail))
