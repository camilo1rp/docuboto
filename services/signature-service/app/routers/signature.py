# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Firma de payloads (sign-xml) y verificación (E06, ADR-016, RF-FIR-11..14).

Autorización: /sign-xml requiere PERM_FIRMA verificado en la BD del tenant
(require_permission, fail-closed). El firmante se toma de X-User-Id (auth_users.id
interno, resuelto por auth-service /validate). Ya NO se valida el JWT aquí — el
api-gateway lo validó y el hash de identidad es el id interno, no el keycloak_sub.

/verify no requiere autenticación — es una operación de consulta pública.

--- Selección de proveedor (Inc.1, ADR-016) ---
``objeto_tipo == "indice"`` y ``settings.signer_provider == "xades_local"``
producen una firma XAdES-B real con el sello institucional del tenant
(:mod:`app.providers.local`); cualquier otro caso cae al HMAC nativo
preexistente (:mod:`app.providers.native`), preservando exactamente el
comportamiento de antes del Incremento 1 (incluida la cadena de firma
secuencial en ``app/routers/cadena.py``, que sigue llamando a
``app.services.signer`` directamente y no pasa por este router).
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.clients import storage_client
from app.core.clearance import get_actor, require_permission
from app.core.config import settings
from app.core.database import get_tenant_conn
from app.core.events import publish_indice_firmado
from app.core.keys import SealNotFoundError, load_seal
from app.core.redis_client import get_redis
from app.core.tsa import TsaUnavailableError
from app.providers import get_provider
from app.providers.base import SignerRef
from app.providers.local import verify_xades
from app.repositories.firma_repository import FirmaRepository
from app.repositories.signer_config_repository import SignerConfigRepository
from app.schemas.firma import (
    FirmaResponse,
    SignXmlRequest,
    VerifyRequest,
    VerifyResponse,
)
from app.services import signer

try:
    from orpycamcp_common import audit as _audit
    _AUDIT_AVAILABLE = True
except ImportError:  # pragma: no cover — common not installed in some test envs
    _audit = None  # type: ignore[assignment]
    _AUDIT_AVAILABLE = False

_SERVICE = "signature-service"
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/signature", tags=["signature"])


def _select_provider_name(objeto_tipo: str) -> str:
    """'xades_local' solo para índices con el proveedor habilitado; 'nativa' en el resto."""
    if objeto_tipo == "indice" and settings.signer_provider == "xades_local":
        return "xades_local"
    return "nativa"


def _native_signer_ref(firmante_id: uuid.UUID) -> SignerRef:
    """Ver la nota de reutilización de campos en app.providers.native."""
    return SignerRef(
        cert_pem=b"",
        key_pem=settings.signature_secret.encode(),
        key_password=str(firmante_id).encode(),
        tipo_firma="nativa",
        nivel_conformidad="firma_electronica_nativa",
        acreditado=False,
    )


@router.post(
    "/sign-xml",
    response_model=FirmaResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("PERM_FIRMA"))],
)
async def sign_xml(
    payload: SignXmlRequest,
    request: Request,
    actor: uuid.UUID = Depends(get_actor),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> FirmaResponse:
    """
    Firma electrónica de un payload XML o hash de documento (RF-FIR-11..14).

    - El firmante se toma exclusivamente de X-User-Id (auth_users.id interno — RF-FIR-11).
    - Se verifica PERM_FIRMA en la BD del tenant antes de llegar aquí (require_permission).
    - El sello_tiempo es generado por el servidor (no lo aporta el cliente).
    - Para ``objeto_tipo="indice"`` con el proveedor XAdES habilitado, produce una
      firma XAdES-B real con el sello institucional del tenant (ver
      ``app.providers.local``); en cualquier otro caso, HMAC-SHA256 nativo
      (RF-FIR-12), no reproducible sin conocer el secreto del servicio.
    - El acto de firma queda registrado en audit_log (RF-FIR-14).

    TODO (RF-FIR-13): reto 2FA ligado al hash_documento — diferido a F4.
    """
    firmante_id = actor
    tenant_slug: str = request.headers.get("X-Tenant-Slug", "")
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
        signer_ref = _native_signer_ref(firmante_id)

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

    minio_key_firmado: str | None = None
    if provider_name == "xades_local":
        filename = f"indice-{payload.objeto_id}-v{payload.version or 0}-firmado.xml"
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

    sello_tiempo_dt = datetime.now(timezone.utc)

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

    row = await FirmaRepository(conn).insert(
        {
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
                # (Inc.4, XAdES-T) Presentes solo cuando LocalSigner selló con
                # TSA (`signed.extra` viene vacío para 'nativa' o XAdES-B
                # "puro" — kill-switch `signer_timestamp_enabled=False`); el
                # hecho del sellado queda en la traza inmutable, no solo en
                # `firma` (append-only por convención, no el registro
                # inmutable). `tsa_acreditado` se fija explícitamente en
                # False para que la traza nunca sobre-declare conformidad
                # (RF-FIR-15) aunque `signed.extra` llegara incompleto.
                "tsa_timestamp": signed.extra.get("tsa_timestamp"),
                "tsa_cert_fingerprint": signed.extra.get("tsa_cert_fingerprint"),
                "tsa_acreditado": False,
            },
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
    # ya persistida (ver docstring de app.core.events).
    if provider_name == "xades_local":
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
    )


@router.post("/verify", response_model=VerifyResponse)
async def verify(
    payload: VerifyRequest,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> VerifyResponse:
    """
    Verifica la integridad de una firma existente.

    No requiere autenticación — es una operación de consulta pública.

    - Firmas ``provider='xades_local'``: primero ata la verificación a ESTA
      firma_id (``signed_xml_sha256``, migración 010 — evita que el
      ``signed_xml`` de OTRO índice firmado con el mismo sello institucional
      del tenant se reporte válido para un ``firma_id`` ajeno); luego valida
      la firma XAdES-B REAL sobre ``payload_xml`` (que debe ser el XML
      **firmado** completo), pasando ``row["tsa_cert_fingerprint"]`` como
      ancla del sello de tiempo — ver ``app.providers.local.verify_xades``
      para el detalle de las comprobaciones restantes (criptografía,
      cobertura de la referencia, vigencia del certificado, huella contra el
      sello registrado al firmar y, si hay ``SignatureTimeStamp``, huella del
      certificado de la TSA contra el ``tsa_cert_fingerprint`` persistido —
      Inc.4, fail-closed cuando la fila no lo tiene registrado).
    - Firmas ``provider='nativa'``: recomputa el HMAC con firmante_id y
      sello_tiempo almacenados en BD y lo compara con la firma_blob
      registrada (comportamiento sin cambios desde antes del Incremento 1).
    """
    row = await FirmaRepository(conn).get(payload.firma_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "firma_not_found", "detail": str(payload.firma_id), "status": 404},
        )

    if row.get("provider") == "xades_local":
        fingerprint = row.get("certificado_fingerprint")
        if not fingerprint:
            return VerifyResponse(
                firma_id=row["id"],
                valida=False,
                provider="xades_local",
                xades_level=row.get("xades_level"),
                nivel_conformidad=row.get("nivel_conformidad"),
                acreditado=False,
                motivo="firma_sin_huella_de_certificado_registrada",
            )
        # Ata la verificación a ESTA firma_id (migración 010): sin esto, el
        # signed_xml de OTRO índice firmado con el MISMO sello institucional
        # del tenant se reportaría `valida=True` para cualquier firma_id de
        # ese tenant. `signed_xml_sha256` es NULL en filas anteriores a la
        # migración — se degrada al comportamiento previo (solo huella de
        # certificado) para no romper la verificación de firmas históricas.
        expected_xml_sha256 = row.get("signed_xml_sha256")
        if expected_xml_sha256 and hashlib.sha256(
            payload.payload_xml.encode()
        ).hexdigest() != expected_xml_sha256:
            return VerifyResponse(
                firma_id=row["id"],
                valida=False,
                provider="xades_local",
                xades_level=row.get("xades_level"),
                nivel_conformidad=row.get("nivel_conformidad"),
                acreditado=False,
                motivo="documento_no_corresponde_a_firma_id",
            )
        outcome = verify_xades(
            payload.payload_xml.encode(),
            fingerprint,
            expected_tsa_fingerprint=row.get("tsa_cert_fingerprint"),
        )
        return VerifyResponse(
            firma_id=row["id"],
            valida=outcome.valida,
            provider="xades_local",
            xades_level=row.get("xades_level"),
            nivel_conformidad=row.get("nivel_conformidad"),
            acreditado=outcome.acreditado,
            motivo=outcome.motivo,
            # (Inc.4) None cuando la firma no lleva SignatureTimeStamp (no
            # aplica, no es un fallo) — ver app.providers.local.VerifyOutcome.
            sello_tiempo_valido=outcome.sello_tiempo_valido,
            tsa_timestamp=outcome.tsa_timestamp,
        )

    firmante_id = str(row["firmante_id"]) if row["firmante_id"] else ""
    sello_tiempo_str = (
        row["sello_tiempo"].astimezone(timezone.utc).isoformat()
        if row["sello_tiempo"]
        else ""
    )

    valida = signer.verificar(
        payload.payload_xml.encode(),
        firmante_id,
        row["firma_blob"],
        sello_tiempo_str,
        settings.signature_secret,
    )
    return VerifyResponse(
        firma_id=row["id"],
        valida=valida,
        provider=row["provider"],
        nivel_conformidad=row.get("nivel_conformidad"),
        acreditado=False,
        motivo=None if valida else "hmac_no_coincide",
    )


@router.get("/firmas/{firma_id}", response_model=FirmaResponse)
async def get_firma(
    firma_id: uuid.UUID,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> FirmaResponse:
    """Metadatos de una firma (certificado público, nivel de conformidad, ...).

    Bajo ``/firmas/{firma_id}`` (no ``/{firma_id}`` a secas) para no colisionar
    con las rutas estáticas GET de ``app/routers/cadena.py`` que comparten el
    mismo prefijo ``/api/v1/signature`` (``/pending``, ``/requests``,
    ``/requests/{solicitud_id}``): un catch-all de un solo segmento aquí
    interceptaría esas rutas si este router se registra antes que el de
    cadena (ver el orden de ``include_router`` en ``app/main.py``).

    Sin datos sensibles: nunca expone una clave privada (solo hay un campo de
    certificado PÚBLICO — ``certificado_pem`` no se incluye en la respuesta,
    solo su huella; el consumidor que necesite el certificado completo lo
    obtiene del propio XML firmado descargado de ``minio_key_firmado``).
    """
    row = await FirmaRepository(conn).get(firma_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "firma_not_found", "detail": str(firma_id), "status": 404},
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
