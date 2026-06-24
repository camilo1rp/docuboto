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


@router.post(
    "/{document_id}/signatures",
    response_model=SignatureResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Sign a radicado/anexo electronically (E06)",
)
async def sign_document(
    document_id: uuid.UUID,
    payload: SignatureCreate,
    request: Request,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> SignatureResponse:
    row = await SignatureRepository(conn).insert(
        radicado_id=document_id,
        anexo_id=payload.anexo_id,
        signer_id=_signer_id(request),
        signer_name=request.headers.get("X-Username"),
        content_hash=payload.content_hash.lower(),
        reason=payload.reason,
    )
    return SignatureResponse(**dict(row))


@router.get(
    "/{document_id}/signatures",
    response_model=list[SignatureResponse],
    summary="List signatures of a radicado (E06)",
)
async def list_signatures(
    document_id: uuid.UUID,
    response: Response,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> list[SignatureResponse]:
    rows = await SignatureRepository(conn).list_by_radicado(document_id)
    response.headers["X-Total-Count"] = str(len(rows))
    return [SignatureResponse(**dict(r)) for r in rows]


@router.get(
    "/{document_id}/signatures/verify",
    response_model=SignatureVerifyResponse,
    summary="Verify a content hash against the radicado's signatures (E06)",
)
async def verify_signature(
    document_id: uuid.UUID,
    content_hash: str,
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> SignatureVerifyResponse:
    normalized = content_hash.lower()
    rows = await SignatureRepository(conn).list_matching(document_id, normalized)
    return SignatureVerifyResponse(
        content_hash=normalized,
        valid=bool(rows),
        signatures=[SignatureResponse(**dict(r)) for r in rows],
    )
