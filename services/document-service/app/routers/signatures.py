# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Firma electrónica de radicados/anexos (E06, ADR-016).

Firma = identidad (claims del gateway) + hash SHA-256 del contenido + sello de
tiempo + motivo. La verificación comprueba que el hash firmado coincide con el
hash del contenido actual (detección de alteración).
"""

import uuid

import asyncpg
from fastapi import APIRouter, Depends, Request, Response, status

from app.core.clearance import require_permission, resolve_caller_clearance
from app.core.database import get_tenant_conn
from app.repositories.signature_repository import SignatureRepository
from app.schemas.signature import (
    SignatureCreate,
    SignatureResponse,
    SignatureVerifyResponse,
)
from app.services.signature_service import SignatureService, not_found

router = APIRouter(prefix="/api/v1/documents", tags=["signatures"])


def _signer_id(request: Request) -> uuid.UUID | None:
    raw = request.headers.get("X-User-Id")
    try:
        return uuid.UUID(raw) if raw else None
    except ValueError:
        return None


@router.post(
    "/{document_id}/signatures",
    response_model=SignatureResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Sign a radicado/anexo electronically (E06)",
    # Nivel 2 (editar), no el de lectura por defecto: firmar es una ESCRITURA
    # probatoria y la hacía quien tiene PERM_FIRMA en modo consulta.
    dependencies=[Depends(require_permission("PERM_FIRMA", min_crud=2))],
)
async def sign_document(
    document_id: uuid.UUID,
    payload: SignatureCreate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> SignatureResponse:
    """Firma declarativa de un anexo (E06): firma electrónica SIMPLE.

    Exige `PERM_FIRMA` a nivel 2 (escritura) y respeta no-read-up: no se puede
    firmar un radicado que no se puede ver. La huella NO la elige el cliente: el
    servidor la resuelve desde el SHA-256 que storage calculó al subir el anexo
    (el `content_hash` del cuerpo se ignora). Registro y asiento
    `document.signature_created` en `public.audit_log` son UN acto
    (misma transacción). La firma con valor probatorio vive en signature-service.
    """
    signer = _signer_id(request)
    clearance = await resolve_caller_clearance(conn, signer)
    return await SignatureService(conn, request.headers.get("X-Tenant-Slug", "")).firmar(
        document_id,
        payload,
        signer=signer,
        signer_name=request.headers.get("X-Username"),
        max_clearance=clearance,
    )


@router.get(
    "/{document_id}/signatures",
    response_model=list[SignatureResponse],
    summary="List signatures of a radicado (E06)",
    dependencies=[Depends(require_permission("PERM_FIRMA"))],
)
async def list_signatures(
    document_id: uuid.UUID,
    response: Response,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[SignatureResponse]:
    """Lista las firmas del radicado (E06).

    No-read-up: 404 neutro si el radicado no existe o supera el clearance; `[]`
    (200) si es visible y aún no tiene firmas. No es oráculo — un sub-clearance
    recibe SIEMPRE 404, nunca el `[]`, que solo alcanza quien ya pasó el gate.
    """
    repo = SignatureRepository(conn)
    clearance = await resolve_caller_clearance(conn, _signer_id(request))
    if not await repo.radicado_visible(document_id, clearance):
        raise not_found(document_id)
    rows = await repo.list_by_radicado(document_id)
    response.headers["X-Total-Count"] = str(len(rows))
    return [SignatureResponse(**dict(r)) for r in rows]


@router.get(
    "/{document_id}/signatures/verify",
    response_model=SignatureVerifyResponse,
    summary="Verify a content hash against the radicado's signatures (E06)",
    dependencies=[Depends(require_permission("PERM_FIRMA"))],
)
async def verify_signature(
    document_id: uuid.UUID,
    content_hash: str,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> SignatureVerifyResponse:
    """Verifica un hash contra las firmas del radicado (E06).

    Gateado igual que el listado: sin él, el `valid` true/false era un **oráculo
    de contenido** — permitía confirmar por fuerza bruta el hash de un documento
    reservado sin llegar a leerlo.
    """
    repo = SignatureRepository(conn)
    clearance = await resolve_caller_clearance(conn, _signer_id(request))
    if not await repo.radicado_visible(document_id, clearance):
        raise not_found(document_id)
    normalized = content_hash.lower()
    rows = await repo.list_matching(document_id, normalized)
    return SignatureVerifyResponse(
        content_hash=normalized,
        valid=bool(rows),
        signatures=[SignatureResponse(**dict(r)) for r in rows],
    )
