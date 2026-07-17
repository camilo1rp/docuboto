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
from datetime import timezone

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.clearance import get_actor, require_permission
from app.core.config import settings
from app.core.constants import SYSTEM_RECONCILER_ID
from app.core.database import get_tenant_conn
from app.core.internal_auth import require_internal_token
from app.providers.local import verify_xades
from app.repositories.firma_repository import FirmaRepository
from app.schemas.firma import (
    FirmaResponse,
    InternalSignIndiceRequest,
    SignXmlRequest,
    VerifyRequest,
    VerifyResponse,
)
from app.services import signer, signing_service

_SERVICE = "signature-service"
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/signature", tags=["signature"])


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
    tenant_slug: str = request.headers.get("X-Tenant-Slug", "")
    return await signing_service.sign_payload(
        conn=conn,
        tenant_slug=tenant_slug,
        payload=payload,
        firmante_id=actor,
        origen="manual",
    )


@router.post(
    "/internal/sign-indice",
    response_model=FirmaResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_internal_token)],
)
async def sign_indice_internal(
    payload: InternalSignIndiceRequest,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> FirmaResponse:
    """Sellado del índice electrónico o del acta de transferencia ATRIBUIDO A
    SISTEMA — reconciliación (residual Ac. AGN 001/2024 art. 4.3.2.4, ver
    archive-service `app/jobs/indice_reconciliation.py`; generalizada en H-H
    E06/F4 para `transferencia_acta`).

    Gate SOLO `require_internal_token` (X-Internal-Token, D-02) — NUNCA
    `require_permission`/`get_actor`: el llamador es un job de reconciliación
    de archive-service, no un usuario humano. `objeto_tipo` está ACOTADO al
    allow-set institucional (`InternalSignIndiceRequest`, validado en el
    schema) — el llamador NUNCA puede elegir un tipo arbitrario, a diferencia
    de `SignXmlRequest.objeto_tipo` en `/sign-xml` — estructuralmente esta
    ruta NUNCA puede firmar la cadena personal (`/requests/{id}/sign`) ni
    ningún objeto fuera de ese conjunto.

    `firmante_id=SYSTEM_RECONCILER_ID` (`app/core/constants.py`) y
    `origen="reconciliation"` en el asiento `firma.creada` — distinguible del
    sellado manual (`/sign-xml`, `origen="manual"`, firmante humano).
    Reutiliza `signing_service.sign_payload` SIN duplicar lógica: mismo
    `prepare` → `load_seal` → `provider.sign` → persistencia + auditoría +
    subida a storage que `/sign-xml`.
    """
    tenant_slug: str = request.headers.get("X-Tenant-Slug", "")
    sign_request = SignXmlRequest(
        payload_xml=payload.payload_xml,
        objeto_tipo=payload.objeto_tipo,
        objeto_id=payload.objeto_id,
        formato="XAdES",
        indice_id=payload.indice_id,
        version=payload.version,
    )
    return await signing_service.sign_payload(
        conn=conn,
        tenant_slug=tenant_slug,
        payload=sign_request,
        firmante_id=SYSTEM_RECONCILER_ID,
        origen="reconciliation",
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

    # xades_personal (E17/F4, Incremento 1 — firma personal PKI por-usuario)
    # verifica con EXACTAMENTE el mismo verify_xades que xades_local: la
    # cadena de confianza vive en el certificado embebido (KeyInfo), no en el
    # `provider` — para xades_personal, `ca_fingerprint`/`ocsp_responder_fingerprint`
    # son siempre None (nunca escala a LT/LTA en este incremento), así que
    # verify_xades simplemente se queda en XAdES-B/T, sin rama especial aquí.
    if row.get("provider") in ("xades_local", "xades_personal"):
        provider_name = row["provider"]
        fingerprint = row.get("certificado_fingerprint")
        if not fingerprint:
            return VerifyResponse(
                firma_id=row["id"],
                valida=False,
                provider=provider_name,
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
                provider=provider_name,
                xades_level=row.get("xades_level"),
                nivel_conformidad=row.get("nivel_conformidad"),
                acreditado=False,
                motivo="documento_no_corresponde_a_firma_id",
            )
        outcome = verify_xades(
            payload.payload_xml.encode(),
            fingerprint,
            expected_tsa_fingerprint=row.get("tsa_cert_fingerprint"),
            expected_ca_fingerprint=row.get("ca_fingerprint"),
            expected_ocsp_responder_fingerprint=row.get("ocsp_responder_fingerprint"),
        )
        return VerifyResponse(
            firma_id=row["id"],
            valida=outcome.valida,
            provider=provider_name,
            # El nivel REALMENTE verificado para ESTE documento (RT-16, Pieza
            # 6.4) — nunca el `xades_level` "intencionado" al firmar. Se cae
            # al persistido en la fila solo si `verify_xades` no llegó a
            # evaluar ningún nivel (p. ej. certificado fuera de vigencia).
            xades_level=outcome.xades_level or row.get("xades_level"),
            nivel_conformidad=row.get("nivel_conformidad"),
            acreditado=outcome.acreditado,
            motivo=outcome.motivo,
            # (Inc.4) None cuando la firma no lleva SignatureTimeStamp (no
            # aplica, no es un fallo) — ver app.providers.local.VerifyOutcome.
            sello_tiempo_valido=outcome.sello_tiempo_valido,
            tsa_timestamp=outcome.tsa_timestamp,
            lt_material_present=outcome.lt_material_present,
            archive_timestamp_present=outcome.archive_timestamp_present,
            revocation_provenance=outcome.revocation_provenance,
            crl_next_update=outcome.crl_next_update,
            ocsp_present=outcome.ocsp_present,
            ocsp_cert_status=outcome.ocsp_cert_status,
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
        lt_material_present=d.get("lt_material_present", False),
        archive_timestamp_present=d.get("archive_timestamp_present", False),
        revocation_provenance=d.get("revocation_provenance"),
        crl_next_update=d.get("crl_next_update"),
        ocsp_present=d.get("ocsp_present", False),
    )
