# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Firma personal (sign-personal), verificación y metadatos de firma (E06, ADR-016, RF-FIR-11..15).

El sello institucional (índice y actas) ya no tiene ruta HTTP: solo lo pide
archive en proceso, por ``signature.api`` (ADR-047 §1). Así no hay superficie
pública que lo alcance, ni token interno que lo proteja.

/verify exige JWT y aplica no-read-up (ver su docstring); la verificación
pública va por ``/api/v1/public/`` con código de verificación (E13). La cadena
de firma personal (``signature/routers/cadena.py``) no pasa por este router.
"""

from __future__ import annotations

import uuid

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status
from orpycamcp_common.clearance import get_actor
from orpycamcp_common.clearance import require_actor_permission as require_permission
from orpycamcp_common.database import get_tenant_conn
from orpycamcp_common.rbac import resolve_caller_clearance
from orpycamcp_common.usuario import id_opcional

from signature.core.constants import NIVELES_CONFORMIDAD_ACREDITADOS
from signature.repositories.firma_repository import FirmaRepository
from signature.schemas.firma import (
    FirmaResponse,
    SignPersonalRequest,
    VerifyRequest,
    VerifyResponse,
)
from signature.services import verification
from signature.services.personal_signing import PersonalSigningService

router = APIRouter(prefix="/api/v1/signature", tags=["signature"])


async def _clearance_del_llamante(request: Request, conn: asyncpg.Connection) -> int | None:
    """Clearance del usuario que consulta, o ``None`` si no hay usuario detrás.

    ``/verify`` también lo llaman servicios internos sin usuario (archive-service
    verificando el sellado de un índice, solo con ``X-Tenant-Slug``): para ellos
    no hay clearance que aplicar. Por el gateway SIEMPRE llega ``X-User-Id``
    (exige JWT), y entonces el gate aplica (AREA-04-10). Un ``X-User-Id`` mal
    formado es 401, no «sin usuario».
    """
    if not (str(u) if (u := id_opcional(request)) else None):
        return None
    actor = await get_actor(request)
    return await resolve_caller_clearance(conn, actor)


@router.post(
    "/sign-personal",
    response_model=FirmaResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_permission("PERM_FIRMA"))],
)
async def sign_personal(
    payload: SignPersonalRequest,
    request: Request,
    actor: uuid.UUID = Depends(get_actor),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> FirmaResponse:
    """
    Firma PERSONAL PKI por-usuario de un ``payload_xml`` ARBITRARIO (E17/F4,
    Incremento 1) — endpoint reutilizable, primer consumidor: el acta de
    entrega de transferencia (``archive-service``,
    ``POST /transferencias/{id}/acta/firmar-personal``).

    A diferencia del sello institucional (``signature.api.sign_xml_outcome``),
    esta ruta SIEMPRE firma con el certificado PERSONAL del actor
    autenticado — requiere una credencial 'activo' (auth-service) y un
    ``totp_code`` válido, verificado-y-consumido atómicamente junto con la
    firma. Gate ``PERM_FIRMA`` (mismo permiso que el resto de rutas de
    firma) + ``X-User-Id``/JWT (``Depends(get_actor)``): el llamante trae
    identidad y TOTP de un humano real. Archive firma el acta por
    ``signature.api.sign_personal``, con el mismo gate.
    """
    tenant_slug: str = request.headers.get("X-Tenant-Slug", "")
    return await PersonalSigningService(conn, tenant_slug).sign(payload, actor)


@router.post("/verify", response_model=VerifyResponse)
async def verify(
    payload: VerifyRequest,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> VerifyResponse:
    """
    Verifica la integridad de una firma existente.

    NO es pública: por el api-gateway exige JWT (``/api/v1/signature/verify`` no
    está en ``PUBLIC_PATHS``) y el tenant sale del token. Dentro de la entidad
    la consulta aplica el no-read-up (RF-SEG-08): la firma de un objeto por
    encima del clearance del llamante da el mismo 404 que un ``firma_id``
    inexistente. Los servicios internos sin usuario (``X-User-Id`` ausente,
    p. ej. archive-service verificando un índice) no tienen clearance que
    aplicar. La verificación verdaderamente pública va por
    ``/api/v1/public/`` con código de verificación (E13).

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
    - Firmas ``provider='xades_personal'`` (E17/F4): mismo ``verify_xades``,
      pero ``row["ca_fingerprint"]`` (si está poblado — Incremento 2, Baja
      #4) se pasa como ``expected_user_ca_fingerprint`` — NUNCA como
      ``expected_ca_fingerprint`` (esa rama es exclusiva del sello
      institucional) — para activar la RAMA PERSONAL de ``verify_xades``.
      Si la firma cripto+cadena valida, ADEMÁS se consulta
      ``GET /internal/signing-key/revocation-status`` (auth-service, en
      línea, NUNCA cacheado) por ``certificado_fingerprint`` — el veredicto
      final ``valida`` incorpora ese resultado (ver
      ``VerifyResponse.revocacion_status``/``firma_criptograficamente_valida``).
      Una firma ``xades_personal`` de Incremento 1 (``ca_fingerprint=NULL``)
      NUNCA activa la rama personal de ``verify_xades`` — retrocompat exacta.
    - Firmas ``provider='nativa'``: recomputa el HMAC con firmante_id y
      sello_tiempo almacenados en BD y lo compara con la firma_blob
      registrada (comportamiento sin cambios desde antes del Incremento 1).
    """
    # No-read-up (AREA-04-10): la firma de un objeto por encima del clearance del
    # llamante da el MISMO 404 que un UUID inexistente — el nivel va dentro del
    # WHERE de la consulta, no en una comprobación posterior que dejaría
    # distinguir «existe» de «no existe».
    row = await FirmaRepository(conn).get(
        payload.firma_id, max_clearance=await _clearance_del_llamante(request, conn)
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "firma_not_found", "detail": str(payload.firma_id), "status": 404},
        )

    return await verification.verificar_firma(
        conn, row, payload.payload_xml, request.headers.get("X-Tenant-Slug", "")
    )


@router.get("/firmas/{firma_id}", response_model=FirmaResponse)
async def get_firma(
    firma_id: uuid.UUID,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> FirmaResponse:
    """Metadatos de una firma (certificado público, nivel de conformidad, ...).

    Bajo ``/firmas/{firma_id}`` (no ``/{firma_id}`` a secas) para no colisionar
    con las rutas estáticas GET de ``signature/routers/cadena.py`` que comparten el
    mismo prefijo ``/api/v1/signature`` (``/pending``, ``/requests``,
    ``/requests/{solicitud_id}``): un catch-all de un solo segmento aquí
    interceptaría esas rutas si este router se registra antes que el de
    cadena (ver el orden de ``include_router`` en ``signature/main.py``).

    Sin datos sensibles: nunca expone una clave privada (solo hay un campo de
    certificado PÚBLICO — ``certificado_pem`` no se incluye en la respuesta,
    solo su huella; el consumidor que necesite el certificado completo lo
    obtiene del propio XML firmado descargado de ``minio_key_firmado``).
    """
    # Mismo no-read-up que `/verify` (AREA-04-10): esta ruta devuelve MÁS datos
    # de la firma (firmante, nivel, huella) y no puede ser el atajo.
    row = await FirmaRepository(conn).get(
        firma_id, max_clearance=await _clearance_del_llamante(request, conn)
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "firma_not_found", "detail": str(firma_id), "status": 404},
        )
    d = dict(row)
    try:
        vigente = await FirmaRepository(conn).artefacto_vigente(firma_id)
    except asyncpg.UndefinedTableError:
        vigente = None
    return FirmaResponse(
        minio_key_vigente=vigente["minio_key"] if vigente else d.get("minio_key_firmado"),
        archive_timestamp_ordinal=vigente["ordinal"] if vigente else 0,
        firma_id=d["id"],
        objeto_tipo=d["objeto_tipo"],
        objeto_id=d.get("objeto_id"),
        formato_solicitado=d.get("formato_solicitado"),
        firmante_id=d.get("firmante_id"),
        firmante_nombre=d.get("firmante_nombre"),
        payload_sha256=d["payload_sha256"],
        provider=d["provider"],
        sello_tiempo=d["sello_tiempo"],
        created_at=d["created_at"],
        minio_key_firmado=d.get("minio_key_firmado"),
        xades_level=d.get("xades_level"),
        nivel_conformidad=d.get("nivel_conformidad"),
        tipo_firma=d.get("tipo_firma"),
        certificado_fingerprint=d.get("certificado_fingerprint"),
        # Lo PERSISTIDO al firmar; esta ruta no re-verifica la criptografía, así
        # que es la declaración de la fila, igual que `nivel_conformidad`.
        acreditado=d.get("nivel_conformidad") in NIVELES_CONFORMIDAD_ACREDITADOS,
        tsa_timestamp=d.get("tsa_timestamp"),
        tsa_acreditado=d.get("tsa_acreditado", False),
        lt_material_present=d.get("lt_material_present", False),
        archive_timestamp_present=d.get("archive_timestamp_present", False),
        revocation_provenance=d.get("revocation_provenance"),
        crl_next_update=d.get("crl_next_update"),
        ocsp_present=d.get("ocsp_present", False),
    )
