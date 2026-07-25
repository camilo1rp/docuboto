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
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.core.clearance import require_permission, resolve_caller_clearance
from app.core.database import get_tenant_conn
from app.repositories.signature_repository import SignatureRepository
from app.schemas.signature import (
    SignatureCreate,
    SignatureResponse,
    SignatureVerifyResponse,
)

router = APIRouter(prefix="/api/v1/documents", tags=["signatures"])


def _signer_id(request: Request) -> uuid.UUID | None:
    raw = request.headers.get("X-User-Id")
    try:
        return uuid.UUID(raw) if raw else None
    except ValueError:
        return None


def _not_found(document_id: uuid.UUID) -> HTTPException:
    """404 NEUTRO: mismo cuerpo para «no existe» y «supera tu clearance».

    Si difirieran, un actor sin nivel podría sondear el servicio para confirmar la
    existencia de radicados reservados (RF-SEG-08, Ley 1712/2014).
    """
    return HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail={"error": "document_not_found", "detail": str(document_id), "status": 404},
    )


@router.post(
    "/{document_id}/signatures",
    response_model=SignatureResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Sign a radicado/anexo electronically (E06)",
    dependencies=[Depends(require_permission("PERM_FIRMA"))],
)
async def sign_document(
    document_id: uuid.UUID,
    payload: SignatureCreate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> SignatureResponse:
    """Firma un radicado/anexo (E06).

    Exige `PERM_FIRMA` (el mismo permiso que gobierna signature-service) y respeta
    no-read-up: no se puede firmar un radicado que no se puede ver.
    """
    clearance = await resolve_caller_clearance(conn, _signer_id(request))
    row = await SignatureRepository(conn).insert(
        radicado_id=document_id,
        anexo_id=payload.anexo_id,
        signer_id=_signer_id(request),
        signer_name=request.headers.get("X-Username"),
        content_hash=payload.content_hash.lower(),
        reason=payload.reason,
        max_clearance=clearance,
    )
    if row is None:
        raise _not_found(document_id)
    return SignatureResponse(**dict(row))


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
        raise _not_found(document_id)
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
        raise _not_found(document_id)
    normalized = content_hash.lower()
    rows = await repo.list_matching(document_id, normalized)
    return SignatureVerifyResponse(
        content_hash=normalized,
        valid=bool(rows),
        signatures=[SignatureResponse(**dict(r)) for r in rows],
    )
