# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Primitivo COMPARTIDO de firma PERSONAL PKI por-usuario (E17/F4).

``sign_personal_xml`` es el extract-method del cuerpo que
``CadenaFirmaService._firmar_personal`` (``app/services/cadena_service.py``,
Incremento 1) ya validaba end-to-end: construye un ``SignerRef`` con la
técnica de "firma remota" (par RSA EFÍMERO + ``remote_sign`` que llama a
``POST /internal/signing-key/sign-digest`` en auth-service, TOTP
verificado-y-consumido ATÓMICAMENTE del lado de auth-service — D1 del
contrato de firma personal) y delega en ``LocalSigner`` (``app.providers.local``).

Dos llamantes, MISMA maquinaria, sin duplicar nada de TSA/LT/OCSP/anclaje de
sub-CA (todo eso vive en ``LocalSigner.sign``, no aquí):

  - ``CadenaFirmaService._firmar_personal`` — envuelve el payload NO-XML de un
    turno de la cadena secuencial (hash/identificador de un radicado) en el
    envoltorio mínimo ``_build_personal_envelope`` ANTES de llamar aquí.
  - ``PersonalSigningService.sign`` (``POST /api/v1/signature/sign-personal``,
    E17/F4 Incremento 1 del acta de transferencia) — el ``payload_xml`` YA es
    un documento XML válido (p. ej. el FUID/acta de entrega congelado por
    archive-service) y se firma TAL CUAL, enveloped, sin envoltorio adicional.

Ninguna lógica de negocio (gates de clearance, persistencia, auditoría, MinIO)
vive aquí — solo el acto criptográfico puro.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from base64 import b64decode
from datetime import UTC, datetime

import asyncpg
from fastapi import HTTPException, status

from app.clients import storage_client
from app.core.clearance import resolve_caller_clearance
from app.core.http_clients import (
    SignDigestRemoteError,
    SignDigestRemoteUnavailableError,
    SigningKeyStatusUnavailableError,
    get_signing_key_status_remote,
    sign_digest_remote,
)
from app.providers.base import SignedDoc, SignerRef
from app.providers.local import LocalSigner
from app.repositories.cadena_repository import CadenaRepository
from app.repositories.firma_repository import FirmaRepository
from app.schemas.firma import FirmaResponse, SignPersonalRequest
from app.services.signing_service import _MINIO_FILENAME_PREFIX

logger = logging.getLogger("signature.personal_signing")

try:
    from orpycamcp_common import audit as _audit

    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover — common not installed in all test envs
    _audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

_SERVICE = "signature-service"


async def sign_personal_xml(
    *,
    xml_bytes: bytes,
    objeto_tipo: str,
    objeto_id: str | None,
    authorization: str | None,
    totp_code: str,
    cert_pem: str,
) -> SignedDoc:
    """Produce una firma XAdES-B/T PERSONAL sobre ``xml_bytes`` YA VÁLIDO
    (enveloped) — ver la nota de módulo para la diferencia entre los dos
    llamantes.

    Lanza ``HTTPException`` (NUNCA otra excepción) en cualquier fallo del
    canal remoto — el llamante decide cómo tratarla (``CadenaFirmaService.
    firmar`` la captura y la devuelve sin lanzar dentro de su transacción
    ambiente; ``PersonalSigningService.sign`` la deja propagar tal cual, no
    hay transacción ambiente que proteger en ese camino).
    """

    async def _remote_sign(digest: bytes) -> bytes:
        try:
            result = await sign_digest_remote(authorization, digest, totp_code)
        except SignDigestRemoteUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "canal_firma_personal_no_disponible",
                    "detail": str(exc),
                    "status": 503,
                },
            ) from exc
        except SignDigestRemoteError as exc:
            # auth-service ya formatea el detail en la forma
            # {"error", "detail", "status"} — se reenvía tal cual (mismos
            # códigos: 422 signing_key_no_activa/totp_no_activo, 423
            # totp_bloqueado, 401 totp_incorrecto).
            raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc
        ca_cert_pem = result.get("ca_cert_pem")
        if ca_cert_pem:
            # Anclaje de la sub-CA de usuarios (Incremento 2, Baja #4) —
            # mutación del MISMO `SignerRef` que `LocalSigner.sign` lee más
            # abajo (ver su docstring). `LocalSigner.sign` RECOMPUTA la huella
            # a partir de este PEM (nunca confía en `result.get("ca_fingerprint")`
            # a ciegas) antes de persistirla como `firma.ca_fingerprint`.
            signer_ref.ca_cert_pem = ca_cert_pem.encode()
        return b64decode(result["signature_value_b64"])

    signer_ref = SignerRef(
        cert_pem=cert_pem.encode(),
        key_pem=b"",
        remote_sign=_remote_sign,
        tipo_firma="personal",
        nivel_conformidad="xades_personal_no_acreditado",
        # RF-FIR-15: la CA de usuarios de este incremento (scripts/gen-user-cert.sh)
        # nunca tiene acreditación ONAC — mismo criterio que el sello institucional.
        acreditado=False,
    )
    provider = LocalSigner()
    prepared = await provider.prepare(xml_bytes, objeto_tipo=objeto_tipo, objeto_id=objeto_id)
    return await provider.sign(prepared, signer_ref)


class PersonalSigningService:
    """``POST /api/v1/signature/sign-personal`` (E17/F4, Incremento 1) —
    firma PERSONAL PKI por-usuario de un ``payload_xml`` ARBITRARIO
    (endpoint reutilizable, primer consumidor: el acta de entrega de
    transferencia, ``archive-service``).

    Autenticado como USUARIO (``X-User-Id``/JWT — ``Depends(get_actor)``,
    mismo patrón que ``app/routers/cadena.py``), NUNCA
    ``require_internal_token``: el llamante trae la identidad Y el TOTP de
    un humano real, a diferencia de ``/internal/sign-indice`` (sistema, sin
    factor humano detrás).
    """

    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self._conn = conn
        self._tenant_slug = tenant_slug
        self._cadena_repo = CadenaRepository(conn)

    async def sign(
        self,
        payload: SignPersonalRequest,
        actor: uuid.UUID,
        authorization: str | None,
    ) -> FirmaResponse:
        """
        Guards (en orden):
        1. Gate de clearance no-read-up (RF-SEG-08, D-15): para
           ``objeto_tipo == "radicado"`` resuelve el nivel VIVO del radicado
           referenciado (mismo criterio de snapshot que
           ``CadenaFirmaService.crear``); para cualquier otro ``objeto_tipo``
           (p. ej. ``acta_transferencia_personal``, sin tabla propia en este
           servicio) el nivel queda en PUBLICA — el gate real de esos objetos
           lo aplica el servicio MEDIADOR (archive-service) antes de llamar
           aquí. 404 neutro, indistinguible de un objeto inexistente.
        2. ``payload.firmante_id`` (opcional, defensivo): si el llamante lo
           declara, DEBE coincidir con el actor derivado del JWT — 403
           ``firmante_no_coincide`` en caso contrario. Nunca sustituye la
           identidad (que siempre viene de ``actor``), solo la corrobora.
        3. Estado de la credencial personal del actor (``GET
           /internal/signing-key/status``, auth-service): sin cert 'activo'
           -> 422 ``sin_certificado_activo`` (accionable: completar el
           enrolamiento offline); canal caído -> 503
           ``canal_firma_personal_no_disponible`` (fail-closed, nunca se
           interpreta un fallo de infraestructura como "sin cert").
        4. Firma (``sign_personal_xml``) — TOTP inválido/bloqueado, canal de
           firma remota caído, etc. propagan tal cual (ver esa función).
        5. Subida a MinIO con el nombre determinista ``firma_id``-incluido
           (patrón H-H) — el ``firma_id`` se minta ANTES de subir para que
           quede coherente con la fila `firma` real (mismo criterio que
           ``signing_service.sign_payload``).
        6. Persistencia + auditoría (``firma.creada``) atómicas.
        """
        # --- 1. Gate de clearance no-read-up ---
        # La resolución NO consulta `objeto_tipo` (texto libre del body): mientras lo
        # hizo, declarar "Radicado" —o cualquier otro tipo— sobre el identificador de
        # un radicado CLASIFICADA saltaba este gate evaluándolo contra nivel 1. Si el
        # identificador resuelve contra un radicado o un expediente, manda su nivel
        # real; si no resuelve, es un objeto sin nivel propio (acta, borrador) y
        # queda en PUBLICA, igual que antes.
        nivel = await self._cadena_repo.resolver_nivel_por_objeto(payload.objeto_id) or 1
        clearance = await resolve_caller_clearance(self._conn, actor)
        if nivel > clearance:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={
                    "error": "object_not_found",
                    "detail": f"No existe el objeto de tipo «{payload.objeto_tipo}» indicado",
                    "status": 404,
                },
            )

        # --- 2. Corroboración defensiva del firmante declarado ---
        if payload.firmante_id is not None and payload.firmante_id != actor:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "firmante_no_coincide",
                    "detail": "firmante_id debe coincidir con el actor autenticado (X-User-Id/JWT)",
                    "status": 403,
                },
            )

        # --- 3. Estado de la credencial personal ---
        try:
            status_body = await get_signing_key_status_remote(authorization)
        except SigningKeyStatusUnavailableError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "canal_firma_personal_no_disponible",
                    "detail": str(exc),
                    "status": 503,
                },
            ) from exc
        if status_body.get("estado") != "activo" or not status_body.get("cert_pem"):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail={
                    "error": "sin_certificado_activo",
                    "detail": (
                        "No tiene un certificado de firma personal activo: complete la "
                        "inscripción y expedición fuera de línea antes de firmar en persona"
                    ),
                    "status": 422,
                },
            )
        # La credencial (y el TOTP que se verificará contra ella) son las del
        # dueño del BEARER; el acto se atribuye a `X-User-Id`. Si no son la misma
        # persona se corta aquí, antes de pedir ningún factor ni firmar (AREA-04-05).
        if str(status_body.get("user_id") or "") != str(actor):
            if _AUDIT_AVAILABLE:
                async with self._conn.transaction():
                    await _audit.append(
                        self._conn,
                        tenant_slug=self._tenant_slug,
                        service=_SERVICE,
                        action="firma.identidad_incoherente",
                        object_type=payload.objeto_tipo,
                        object_ref=payload.objeto_id,
                        actor=str(actor),
                        payload={"via": "signing_key_status", "ruta": "sign-personal"},
                        canal=_audit.CANAL_PETICION,
                    )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail={
                    "error": "identidad_incoherente",
                    "detail": (
                        "La credencial presentada no corresponde al usuario al que se "
                        "atribuye la firma"
                    ),
                    "status": 403,
                },
            )
        cert_pem = status_body["cert_pem"]

        # --- 4. Firma (propaga HTTPException tal cual en cualquier fallo) ---
        xml_bytes = payload.payload_xml.encode()
        signed = await sign_personal_xml(
            xml_bytes=xml_bytes,
            objeto_tipo=payload.objeto_tipo,
            objeto_id=payload.objeto_id,
            authorization=authorization,
            totp_code=payload.totp_code,
            cert_pem=cert_pem,
        )

        # --- 5. Subida a MinIO (firma_id pre-minteado, patrón H-H) ---
        firma_id = uuid.uuid4()
        prefix = _MINIO_FILENAME_PREFIX.get(payload.objeto_tipo, payload.objeto_tipo)
        filename = f"{prefix}-{payload.objeto_id}-v{payload.version or 0}-{firma_id}-firmado.xml"
        try:
            minio_key_firmado = await storage_client.upload_signed_xml(
                tenant_slug=self._tenant_slug,
                filename=filename,
                content=signed.signed_xml,
                uploaded_by=actor,
            )
        except storage_client.StorageUploadError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "signed_document_upload_failed",
                    "detail": str(exc),
                    "status": 503,
                },
            ) from exc

        signed_xml_sha256 = hashlib.sha256(signed.signed_xml).hexdigest()
        sello_tiempo_dt = datetime.now(UTC)
        tsa_timestamp_iso = signed.extra.get("tsa_timestamp")

        # --- 6. Persistencia + auditoría atómicas (RF-FIR-14) ---
        async with self._conn.transaction():
            row = await FirmaRepository(self._conn).insert(
                {
                    "id": firma_id,
                    "objeto_tipo": payload.objeto_tipo,
                    "objeto_id": payload.objeto_id,
                    "formato_solicitado": "XAdES",
                    "firmante_id": actor,
                    "payload_sha256": signed.payload_sha256,
                    "firma_blob": signed.signature_value_b64,
                    "provider": signed.provider,
                    "sello_tiempo": sello_tiempo_dt,
                    "certificado_pem": signed.certificado_pem or None,
                    "minio_key_firmado": minio_key_firmado,
                    "nivel_conformidad": signed.nivel_conformidad,
                    "xades_level": signed.xades_level,
                    "tipo_firma": "personal",
                    "certificado_fingerprint": signed.certificado_fingerprint,
                    "signed_xml_sha256": signed_xml_sha256,
                    "tsa_token": signed.extra.get("tsa_token"),
                    "tsa_timestamp": (
                        datetime.fromisoformat(tsa_timestamp_iso) if tsa_timestamp_iso else None
                    ),
                    "tsa_cert_fingerprint": signed.extra.get("tsa_cert_fingerprint"),
                    "tsa_acreditado": signed.extra.get("tsa_acreditado", False),
                    "ca_fingerprint": signed.extra.get("ca_fingerprint"),
                }
            )

            if _AUDIT_AVAILABLE:
                await _audit.append(
                    self._conn,
                    tenant_slug=self._tenant_slug,
                    service=_SERVICE,
                    action="firma.creada",
                    object_type=payload.objeto_tipo,
                    object_ref=payload.objeto_id,
                    actor=str(actor),
                    payload={
                        "firma_id": str(row["id"]),
                        "objeto_tipo": payload.objeto_tipo,
                        "provider": signed.provider,
                        "tipo_firma": "personal",
                        "nivel_conformidad": signed.nivel_conformidad,
                        "xades_level": signed.xades_level,
                        "certificado_fingerprint": signed.certificado_fingerprint,
                        "ca_fingerprint": signed.extra.get("ca_fingerprint"),
                        "indice_id": payload.indice_id,
                        "version": payload.version,
                        "origen": "manual",
                    },
                    canal=_audit.CANAL_PETICION,
                )

        d = dict(row)
        return FirmaResponse(
            firma_id=d["id"],
            objeto_tipo=d["objeto_tipo"],
            objeto_id=d.get("objeto_id"),
            formato_solicitado=d.get("formato_solicitado"),
            firmante_id=d.get("firmante_id"),
            payload_sha256=d["payload_sha256"],
            provider=d["provider"],
            sello_tiempo=d["sello_tiempo"],
            created_at=d["created_at"],
            minio_key_firmado=d.get("minio_key_firmado"),
            xades_level=d.get("xades_level"),
            nivel_conformidad=d.get("nivel_conformidad"),
            tipo_firma=d.get("tipo_firma"),
            certificado_fingerprint=d.get("certificado_fingerprint"),
            acreditado=False,
            tsa_timestamp=d.get("tsa_timestamp"),
            tsa_acreditado=d.get("tsa_acreditado", False),
        )
