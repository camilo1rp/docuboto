# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Núcleo de sellado (XAdES-B/T/LT/LTA institucional del índice, o HMAC
'nativa' de servicio) — E06, ADR-016, RF-FIR-11..14.

Extract-method PURO desde ``app/routers/signature.py::sign_xml`` (residual
Ac. AGN 001/2024 art. 4.3.2.4, reconciliación): antes de este incremento todo
este cuerpo vivía inline en el handler HTTP de ``POST /sign-xml``. Se factoriza
aquí para que la nueva ruta interna ``POST /internal/sign-indice``
(reconciliación, gate ``require_internal_token`` — ver
``app/core/internal_auth.py``) reutilice EXACTAMENTE la misma lógica de
sellado sin duplicarla: mismo orden de pasos, mismas excepciones, mismo
asiento de auditoría ``firma.creada``.

El ÚNICO comportamiento nuevo (aditivo) es el parámetro ``origen`` — 'manual'
por defecto (lo que emite ``/sign-xml``) o 'reconciliation' (lo que emite
``/internal/sign-indice``) — que viaja en el payload del asiento
``firma.creada`` para que ``audit_log`` distinga un sellado manual de uno
producido por el job de reconciliación de archive-service, ambos atribuibles
por ``firmante_id`` (humano vs. ``SYSTEM_RECONCILER_ID``).
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime

import asyncpg
from fastapi import HTTPException, status

from app.clients import storage_client
from app.core.config import settings
from app.core.constants import XADES_ELIGIBLE_OBJETO_TIPOS
from app.core.events import publish_indice_firmado
from app.core.keys import SealNotFoundError, load_seal
from app.core.ocsp import OcspMaterialRequiredError
from app.core.redis_client import get_redis
from app.core.secret_derivation import KEY_VERSION_SIN_HMAC
from app.core.tsa import TsaUnavailableError
from app.providers import get_provider
from app.providers.base import SignerRef
from app.providers.local import (
    LtMaterialRequiredError,
    SelloInstitucionalVencidoError,
    XmldsigNoConformeError,
)
from app.repositories.firma_repository import FirmaRepository
from app.repositories.signer_config_repository import SignerConfigRepository
from app.schemas.firma import FirmaResponse, SignXmlRequest

try:
    from orpycamcp_common import audit as _audit

    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover — common not installed in some test envs
    _audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

_SERVICE = "signature-service"
logger = logging.getLogger(__name__)

# Prefijo del filename subido a storage-service (H-H, E06/F4) — clave EXACTA
# de `XADES_ELIGIBLE_OBJETO_TIPOS`: el único punto que necesita distinguir
# `objeto_tipo` dentro del tramo `xades_local` además del evento Redis más
# abajo. Indexación directa (no `.get`): `provider_name == "xades_local"`
# implica `payload.objeto_tipo in XADES_ELIGIBLE_OBJETO_TIPOS` por
# construcción de `_select_provider_name`, así que esta clave SIEMPRE existe.
#
# `acta_transferencia_personal` (E17/F4, Incremento 1) es AJENA al tramo
# `xades_local`/`XADES_ELIGIBLE_OBJETO_TIPOS` de arriba — la usa
# `app.services.personal_signing.PersonalSigningService.sign`
# (`POST /sign-personal`), que sube a MinIO con el mismo esquema determinista
# `{prefix}-{objeto_id}-v{version}-{firma_id}-firmado.xml` (patrón H-H) pero
# NUNCA indexa este dict directamente (usa `.get(objeto_tipo, objeto_tipo)`,
# tolerante a cualquier `objeto_tipo` arbitrario que declare el llamante —
# `/sign-personal` no está acotado a un allow-set, a diferencia de
# `/internal/sign-indice`).
_MINIO_FILENAME_PREFIX = {
    "indice": "indice",
    "acta_transferencia": "acta-transferencia",
    "acta_transferencia_personal": "acta-transferencia-personal",
    "acta_eliminacion": "acta-eliminacion",
}


def _select_provider_name(objeto_tipo: str) -> str:
    """'xades_local' solo para `objeto_tipo` del allow-set institucional
    (`XADES_ELIGIBLE_OBJETO_TIPOS` — índice, y desde H-H el acta de
    transferencia) con el proveedor habilitado; 'nativa' en el resto,
    comportamiento sin cambios desde antes de este incremento."""
    if objeto_tipo in XADES_ELIGIBLE_OBJETO_TIPOS and settings.signer_provider == "xades_local":
        return "xades_local"
    return "nativa"


def _native_signer_ref(firmante_id: uuid.UUID, tenant_slug: str) -> SignerRef:
    """Ver la nota de reutilización de campos en app.providers.native."""
    return SignerRef(
        cert_pem=b"",
        key_pem=settings.signature_secret.encode(),
        key_password=str(firmante_id).encode(),
        tipo_firma="nativa",
        nivel_conformidad="firma_electronica_nativa",
        acreditado=False,
        tenant_slug=tenant_slug,
    )


async def sign_payload(
    *,
    conn: asyncpg.Connection,
    tenant_slug: str,
    payload: SignXmlRequest,
    firmante_id: uuid.UUID,
    origen: str = "manual",
) -> FirmaResponse:
    """
    Firma electrónica de un payload XML o hash de documento (RF-FIR-11..14).

    - El firmante viaja como parámetro (``firmante_id``) — el llamador HTTP
      lo resuelve: ``X-User-Id`` para ``/sign-xml`` (humano, RF-FIR-11),
      ``SYSTEM_RECONCILER_ID`` para ``/internal/sign-indice`` (sistema, sin
      usuario detrás — ``app/core/constants.py``).
    - El sello_tiempo es generado por el servidor (no lo aporta el cliente).
    - Para ``objeto_tipo="indice"`` con el proveedor XAdES habilitado, produce una
      firma XAdES-B real con el sello institucional del tenant (ver
      ``app.providers.local``); en cualquier otro caso, HMAC-SHA256 nativo
      (RF-FIR-12), no reproducible sin conocer el secreto del servicio.
    - El acto de firma queda registrado en audit_log (RF-FIR-14), con
      ``origen`` ('manual'/'reconciliation') en el payload.

    TODO (RF-FIR-13): reto 2FA ligado al hash_documento — diferido a F4.
    """
    provider_name = _select_provider_name(payload.objeto_tipo)
    provider = get_provider(provider_name)

    prepared = await provider.prepare(
        payload.payload_xml.encode(),
        objeto_tipo=payload.objeto_tipo,
        objeto_id=payload.objeto_id,
        indice_id=payload.indice_id,
        version=payload.version,
    )

    if provider_name == "xades_local":
        try:
            signer_ref = load_seal(tenant_slug)
        except (ValueError, SealNotFoundError) as exc:
            # Fallo de infraestructura (sello no aprovisionado / slug inválido),
            # no un error del cliente — el llamador (archive-service) trata
            # /sign-xml como best-effort y deja el índice 'pendiente_firma'.
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "signing_seal_unavailable",
                    "detail": str(exc),
                    "status": 503,
                },
            ) from exc
    else:
        signer_ref = _native_signer_ref(firmante_id, tenant_slug)

    try:
        signed = await provider.sign(prepared, signer_ref)
    except ValueError as exc:
        # p. ej. `payload_xml` con DOCTYPE (hardening XXE/DoS, RT-16) — ver
        # `app.providers.local._parse_no_doctype`. Ya pasó PERM_FIRMA, pero un
        # payload adversarial de un llamador autorizado sigue siendo rechazado.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"error": "payload_xml_invalido", "detail": str(exc), "status": 400},
        ) from exc
    except TsaUnavailableError as exc:
        # Fallo de infraestructura (Inc.4, RT-16): con
        # settings.signer_timestamp_enabled=True (default) la ausencia de TSA
        # (local sin par montado, o remota configurada sin cliente
        # implementado) tumba la firma completa — fail-closed, mismo trato
        # que SealNotFoundError arriba. El índice queda 'pendiente_firma'
        # aguas arriba (llamador best-effort).
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "timestamp_authority_unavailable",
                "detail": str(exc),
                "status": 503,
            },
        ) from exc
    except LtMaterialRequiredError as exc:
        # Fallo de infraestructura (XAdES-LT/LTA, RT-16): con
        # settings.signer_lt_require=True, la ausencia de material de CA local
        # (ca.crt/crl.der) tumba la firma completa — mismo trato que
        # TsaUnavailableError. Con el flag en False (default), el proveedor NO
        # lanza esta excepción: degrada en silencio-pero-observable a XAdES-T
        # (ver `firma.lt_downgrade_no_ca` más abajo).
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "lt_material_required",
                "detail": str(exc),
                "status": 503,
            },
        ) from exc
    except SelloInstitucionalVencidoError as exc:
        # ARC-M2: el sello institucional caducó; ANTES de firmar nada. Fallo de
        # infraestructura (el tenant debe renovar su sello), no del cliente: el llamador
        # best-effort deja el índice 'pendiente_firma' y no se archiva (WORM) algo
        # que nace inverificable.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "sello_institucional_vencido",
                "detail": str(exc),
                "status": 503,
            },
        ) from exc
    except XmldsigNoConformeError as exc:
        # ADR-034: la firma recién producida no pasó la autoverificación con un
        # verificador XMLDSig por defecto. Nada se sella ni se persiste: un
        # tercero no podría comprobarla.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "firma_no_conforme_xmldsig",
                "detail": str(exc),
                "status": 503,
            },
        ) from exc
    except OcspMaterialRequiredError as exc:
        # Fallo de infraestructura (OCSP stapled, RF-FIR-15): con
        # settings.signer_ocsp_require=True, la ausencia de material de
        # responder OCSP (ocsp.crt/ocsp.key) tumba la firma completa — mismo
        # trato que LtMaterialRequiredError. Con el flag en False (default),
        # el proveedor NO lanza esta excepción: degrada en
        # silencio-pero-observable (ver `firma.ocsp_downgrade_no_responder`
        # más abajo).
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={
                "error": "ocsp_material_required",
                "detail": str(exc),
                "status": 503,
            },
        ) from exc

    minio_key_firmado: str | None = None
    firma_id: uuid.UUID | None = None
    if provider_name == "xades_local":
        # Generado ACÁ (no dejado al DEFAULT de columna en el INSERT de más
        # abajo) para que el nombre del objeto en MinIO incluya el firma_id
        # ANTES de subir (S1, corrección de verify bajo carrera): dos
        # sellados exitosos concurrentes de la misma entidad (intento
        # síncrono post-commit vs. job de reconciliación) ya NO compiten por
        # la misma key — cada firma sube su PROPIO XML y `minio_key_firmado`
        # queda coherente con el `id` real de su fila (ver
        # FirmaRepository.insert, que usa este valor en vez de
        # `gen_random_uuid()` cuando se provee).
        firma_id = uuid.uuid4()
        prefix = _MINIO_FILENAME_PREFIX[payload.objeto_tipo]
        filename = f"{prefix}-{payload.objeto_id}-v{payload.version or 0}-{firma_id}-firmado.xml"
        try:
            minio_key_firmado = await storage_client.upload_signed_xml(
                tenant_slug=tenant_slug,
                filename=filename,
                content=signed.signed_xml,
                uploaded_by=firmante_id,
            )
        except storage_client.StorageUploadError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "error": "signed_index_upload_failed",
                    "detail": str(exc),
                    "status": 503,
                },
            ) from exc

    # El instante que persiste la fila TIENE que ser el que quedó dentro del HMAC
    # de la firma nativa (`extra["sello_tiempo"]`, lo fija `NativeSigner.sign`):
    # `/verify` recomputa el HMAC con `firma.sello_tiempo`, y un `now()` tomado
    # aquí era OTRO instante, así que ninguna firma nativa de `/sign-xml` llegó
    # nunca a verificar. Los demás proveedores no lo declaran y usan el reloj.
    sello_tiempo_iso = signed.extra.get("sello_tiempo")
    sello_tiempo_dt = (
        datetime.fromisoformat(sello_tiempo_iso) if sello_tiempo_iso else datetime.now(UTC)
    )

    # Ata /verify a ESTA firma_id (migración 010): la huella del XML YA
    # FIRMADO (no la de payload_sha256, que es pre-firma — ver la nota de
    # canonicalización en app.providers.local) solo aplica a xades_local, que
    # es el único proveedor que envuelve un documento firmado verificable
    # fuera de banda.
    signed_xml_sha256 = (
        hashlib.sha256(signed.signed_xml).hexdigest() if provider_name == "xades_local" else None
    )

    # XAdES-T (Inc.4) — `signed.extra` solo trae estas claves cuando
    # LocalSigner realmente selló con TSA (`SignatureFormat.XADES_T`); para
    # 'nativa' o XAdES-B "puro" (kill-switch signer_timestamp_enabled=False)
    # `extra` está vacío y todo queda None/False (columnas nullable, migración 012).
    tsa_timestamp_iso = signed.extra.get("tsa_timestamp")
    tsa_timestamp_dt = datetime.fromisoformat(tsa_timestamp_iso) if tsa_timestamp_iso else None

    # XAdES-LT/LTA (RT-16) — presentes en `signed.extra` SOLO cuando
    # LocalSigner escaló a XAdES-LTA (settings.signer_lt_enabled=True Y
    # material de CA local montado); ausentes para cualquier otro caso
    # (columnas nullable, migración 013).
    crl_this_update_iso = signed.extra.get("crl_this_update")
    crl_this_update_dt = datetime.fromisoformat(crl_this_update_iso) if crl_this_update_iso else None
    crl_next_update_iso = signed.extra.get("crl_next_update")
    crl_next_update_dt = datetime.fromisoformat(crl_next_update_iso) if crl_next_update_iso else None
    archive_timestamp_at_iso = signed.extra.get("archive_timestamp_at")
    archive_timestamp_at_dt = (
        datetime.fromisoformat(archive_timestamp_at_iso) if archive_timestamp_at_iso else None
    )

    # OCSP stapled (RF-FIR-15) — presentes en `signed.extra` SOLO cuando
    # LocalSigner realmente construyó una BasicOCSPResponse
    # (settings.signer_ocsp_enabled=True Y material de responder OCSP
    # montado); ausentes para cualquier otro caso (columnas nullable,
    # migración 014).
    ocsp_produced_at_iso = signed.extra.get("ocsp_produced_at")
    ocsp_produced_at_dt = datetime.fromisoformat(ocsp_produced_at_iso) if ocsp_produced_at_iso else None
    ocsp_this_update_iso = signed.extra.get("ocsp_this_update")
    ocsp_this_update_dt = datetime.fromisoformat(ocsp_this_update_iso) if ocsp_this_update_iso else None
    ocsp_next_update_iso = signed.extra.get("ocsp_next_update")
    ocsp_next_update_dt = datetime.fromisoformat(ocsp_next_update_iso) if ocsp_next_update_iso else None

    # La firma persistida y su traza inmutable deben ser atómicas (RF-FIR-14):
    # si `_audit.append` fallara DESPUÉS del INSERT, sin esta transacción
    # asyncpg ya habría auto-commiteado el INSERT de `firma` y la firma
    # quedaría sin rastro en `audit_log`. `_audit.append` ya está diseñado
    # para anidarse (abre su propia (sub)transacción vía savepoint — ver su
    # docstring), así que envolver aquí es seguro. El upload a MinIO y el
    # sellado ya ocurrieron antes de este bloque (no son objeto de rollback);
    # la publicación del evento Redis (`publish_indice_firmado`, más abajo)
    # queda deliberadamente FUERA — es best-effort y un fallo de Redis no
    # debe revertir una firma ya trazada.
    async with conn.transaction():
        row = await FirmaRepository(conn).insert(
            {
                "id": firma_id,
                "objeto_tipo": payload.objeto_tipo,
                "objeto_id": payload.objeto_id,
                "formato_solicitado": payload.formato,
                "firmante_id": firmante_id,
                "payload_sha256": signed.payload_sha256,
                "firma_blob": signed.signature_value_b64,
                "provider": signed.provider,
                "sello_tiempo": sello_tiempo_dt,
                "certificado_pem": signed.certificado_pem or None,
                "minio_key_firmado": minio_key_firmado,
                "nivel_conformidad": signed.nivel_conformidad,
                "xades_level": signed.xades_level,
                "tipo_firma": signer_ref.tipo_firma,
                "certificado_fingerprint": signed.certificado_fingerprint,
                "signed_xml_sha256": signed_xml_sha256,
                "tsa_token": signed.extra.get("tsa_token"),
                "tsa_timestamp": tsa_timestamp_dt,
                "tsa_cert_fingerprint": signed.extra.get("tsa_cert_fingerprint"),
                "tsa_acreditado": signed.extra.get("tsa_acreditado", False),
                "lt_material_present": signed.extra.get("lt_material_present", False),
                "ca_fingerprint": signed.extra.get("ca_fingerprint"),
                "crl_fingerprint": signed.extra.get("crl_fingerprint"),
                "crl_this_update": crl_this_update_dt,
                "crl_next_update": crl_next_update_dt,
                "archive_timestamp_present": signed.extra.get("archive_timestamp_present", False),
                "archive_timestamp_at": archive_timestamp_at_dt,
                "archive_tsa_cert_fingerprint": signed.extra.get("archive_tsa_cert_fingerprint"),
                "revocation_provenance": signed.extra.get("revocation_provenance"),
                "archive_timestamp_token": signed.extra.get("archive_timestamp_token"),
                "ocsp_present": signed.extra.get("ocsp_present", False),
                "ocsp_responder_fingerprint": signed.extra.get("ocsp_responder_fingerprint"),
                "ocsp_cert_status": signed.extra.get("ocsp_cert_status"),
                "ocsp_produced_at": ocsp_produced_at_dt,
                "ocsp_this_update": ocsp_this_update_dt,
                "ocsp_next_update": ocsp_next_update_dt,
                "ocsp_response_der": signed.extra.get("ocsp_response_der"),
                "key_version": signed.extra.get("key_version", KEY_VERSION_SIN_HMAC),
            }
        )

        # Immutable audit record — RF-FIR-14, ADR-008.
        if _AUDIT_AVAILABLE:
            await _audit.append(
                conn,
                tenant_slug=tenant_slug,
                service=_SERVICE,
                action="firma.creada",
                object_type=payload.objeto_tipo,
                object_ref=str(payload.objeto_id) if payload.objeto_id else None,
                actor=str(firmante_id),
                payload={
                    "firma_id": str(row["id"]),
                    "objeto_tipo": payload.objeto_tipo,
                    "provider": signed.provider,
                    "tipo_firma": signer_ref.tipo_firma,
                    "nivel_conformidad": signed.nivel_conformidad,
                    "xades_level": signed.xades_level,
                    "certificado_fingerprint": signed.certificado_fingerprint,
                    "indice_id": payload.indice_id,
                    "version": payload.version,
                    # Distingue un sellado MANUAL (/sign-xml, 'manual') de uno
                    # producido por el job de reconciliación de archive-service
                    # (/internal/sign-indice, 'reconciliation') — residual Ac.
                    # AGN 001/2024 art. 4.3.2.4. Campo ADITIVO: no reemplaza
                    # `firmante_id` (que ya distingue humano vs.
                    # SYSTEM_RECONCILER_ID), solo lo hace explícito sin
                    # obligar al consumidor a conocer esa constante.
                    "origen": origen,
                    # (Inc.4, XAdES-T) Presentes solo cuando LocalSigner selló con
                    # TSA (`signed.extra` viene vacío para 'nativa' o XAdES-B
                    # "puro" — kill-switch `signer_timestamp_enabled=False`); el
                    # hecho del sellado queda en la traza inmutable, no solo en
                    # `firma` (append-only por convención, no el registro
                    # inmutable). `tsa_acreditado` se lee de `signed.extra` con
                    # DEFAULT False: si el firmador no lo declaró, la traza no
                    # se lo inventa (RF-FIR-15, nunca sobre-declarar). Antes era
                    # un `False` fijo, y eso hacía que la traza CONTRADIJERA a
                    # la fila de `firma` en cuanto hubiera una TSA acreditada:
                    # dos registros del mismo acto diciendo cosas distintas.
                    "tsa_timestamp": signed.extra.get("tsa_timestamp"),
                    "tsa_cert_fingerprint": signed.extra.get("tsa_cert_fingerprint"),
                    "tsa_acreditado": signed.extra.get("tsa_acreditado", False),
                    # (XAdES-LT/LTA, RT-16) Presentes solo cuando LocalSigner
                    # escaló a XAdES-LTA — ausentes para 'nativa', XAdES-B/-T o
                    # cualquier degradación (ver el evento separado más abajo).
                    "lt_material_present": signed.extra.get("lt_material_present", False),
                    "ca_fingerprint": signed.extra.get("ca_fingerprint"),
                    "archive_timestamp_present": signed.extra.get("archive_timestamp_present", False),
                    "revocation_provenance": signed.extra.get("revocation_provenance"),
                    # (OCSP stapled, RF-FIR-15) Gemelo del residual que el
                    # sello de tiempo cerró en Inc.4: presentes solo cuando
                    # LocalSigner realmente construyó una BasicOCSPResponse
                    # (ausentes para cualquier otro caso, igual que en la fila
                    # `firma`, migración 014). Sin `acreditado` propio aquí —
                    # el responder es delegado, no acreditado ONAC (ver
                    # docstring de app.core.ocsp, "Conformidad"); no se
                    # sobre-declara, mismo criterio que `tsa_acreditado` arriba.
                    "ocsp_present": signed.extra.get("ocsp_present", False),
                    "ocsp_cert_status": signed.extra.get("ocsp_cert_status"),
                    "ocsp_responder_fingerprint": signed.extra.get("ocsp_responder_fingerprint"),
                },
                canal=_audit.CANAL_PETICION,
            )

            # Evento de auditoría DEDICADO para la degradación observable
            # (RT-16, Pieza 3): `settings.signer_lt_enabled=True` pero el tenant
            # no tiene material CA (ca.crt/crl.der) montado — el índice se firmó
            # igualmente (se quedó en XAdES-T), pero un operador que audite
            # `audit_log` debe poder detectar esta postura sin inspeccionar cada
            # XML firmado. Nunca se emite si `signer_lt_enabled=False` (el
            # feature está simplemente apagado, no es una degradación).
            if signed.extra.get("lt_downgrade_no_ca"):
                await _audit.append(
                    conn,
                    tenant_slug=tenant_slug,
                    service=_SERVICE,
                    action="firma.lt_downgrade_no_ca",
                    object_type=payload.objeto_tipo,
                    object_ref=str(payload.objeto_id) if payload.objeto_id else None,
                    actor=str(firmante_id),
                    payload={
                        "firma_id": str(row["id"]),
                        "xades_level": signed.xades_level,
                        "signer_lt_enabled": settings.signer_lt_enabled,
                        "motivo": "sin_material_ca_local",
                    },
                    canal=_audit.CANAL_PETICION,
                )

            # Evento de auditoría DEDICADO para la degradación observable de
            # OCSP (RF-FIR-15) — análogo a `firma.lt_downgrade_no_ca`:
            # settings.signer_ocsp_enabled=True pero el tenant no tiene
            # material de responder OCSP (ocsp.crt/ocsp.key) montado — la
            # firma XAdES-LT/LTA se produjo igual (sin OCSPValues). Nunca se
            # emite si signer_ocsp_enabled=False (el feature está apagado,
            # no es una degradación) ni si la firma nunca llegó al bloque LT
            # (p. ej. degradó antes a XAdES-T por falta de material CA).
            if signed.extra.get("ocsp_downgrade_no_responder"):
                await _audit.append(
                    conn,
                    tenant_slug=tenant_slug,
                    service=_SERVICE,
                    action="firma.ocsp_downgrade_no_responder",
                    object_type=payload.objeto_tipo,
                    object_ref=str(payload.objeto_id) if payload.objeto_id else None,
                    actor=str(firmante_id),
                    payload={
                        "firma_id": str(row["id"]),
                        "xades_level": signed.xades_level,
                        "signer_ocsp_enabled": settings.signer_ocsp_enabled,
                        "motivo": "sin_material_responder_ocsp",
                    },
                    canal=_audit.CANAL_PETICION,
                )

    # Bookkeeping best-effort del sello vigente (migración 009 — no crítico).
    if provider_name == "xades_local":
        try:
            await SignerConfigRepository(conn).upsert(
                tenant_id=tenant_slug,
                provider=signed.provider,
                cert_fingerprint=signed.certificado_fingerprint,
                acreditado=signer_ref.acreditado,
            )
        except Exception:
            logger.warning(
                "No se pudo actualizar signer_provider_config para tenant=%s",
                tenant_slug,
                exc_info=True,
            )

    # Evento best-effort — un fallo de Redis nunca revierte ni bloquea una firma
    # ya persistida (ver docstring de app.core.events). GATEADO por tipo
    # (H-H, E06/F4): `firma.indice.firmado` tiene un ÚNICO consumidor (E15,
    # el índice electrónico) — el acta de transferencia NO tiene consumidor
    # de este evento, así que NUNCA se publica para `objeto_tipo=
    # "acta_transferencia"` (evita fabricar un evento sin oyente).
    if provider_name == "xades_local" and payload.objeto_tipo == "indice":
        try:
            await publish_indice_firmado(
                get_redis(),
                tenant_slug=tenant_slug,
                actor=str(firmante_id),
                firma_id=str(row["id"]),
                expediente_id=payload.objeto_id,
                indice_id=payload.indice_id,
                version=payload.version,
                minio_key_firmado=minio_key_firmado,
                nivel_conformidad=signed.nivel_conformidad,
                xades_level=signed.xades_level,
            )
        except Exception:
            logger.warning(
                "No se pudo publicar firma.indice.firmado para firma_id=%s",
                row["id"],
                exc_info=True,
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
        acreditado=signer_ref.acreditado,
        tsa_timestamp=d.get("tsa_timestamp"),
        tsa_acreditado=d.get("tsa_acreditado", False),
        lt_material_present=d.get("lt_material_present", False),
        archive_timestamp_present=d.get("archive_timestamp_present", False),
        revocation_provenance=d.get("revocation_provenance"),
        crl_next_update=d.get("crl_next_update"),
        ocsp_present=d.get("ocsp_present", False),
    )
