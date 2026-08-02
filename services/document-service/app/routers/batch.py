# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Batch API endpoints for bulk document operations."""

from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.clearance import require_permission
from app.core.database import get_tenant_conn as get_db
from app.schemas.batch import (
    BatchDocumentsRequest,
    BatchJobResponse,
    BatchJobStatusResponse,
)
from app.services.batch_service import BatchService

router = APIRouter(prefix="/api/v1/batch", tags=["batch"])


@router.post(
    "/documents",
    response_model=BatchJobResponse,
    status_code=202,
    dependencies=[Depends(require_permission("PERM_RADI"))],
)
async def create_batch_documents(
    request: Request,
    payload: BatchDocumentsRequest,
    conn: asyncpg.Connection = Depends(get_db),
) -> BatchJobResponse:
    """
    Create a batch job for multiple documents (radicados).

    Accepts 1-1000 documents. Processing is asynchronous.
    Returns job_id for status tracking with GET /batch/documents/{job_id}/status.

    **Response 202 Accepted** — job queued for processing.
    """
    tenant_slug = request.headers.get("X-Tenant-Slug", "")
    user_id = request.headers.get("X-User-Id", "")

    if not tenant_slug or not user_id:
        raise HTTPException(status_code=400, detail="Missing tenant or user context")

    import asyncio

    service = BatchService(conn, tenant_slug)
    job_id = await service.create_batch_documents(payload, UUID(user_id))
    asyncio.create_task(service.process_batch_documents(job_id))

    created_at = await conn.fetchval("SELECT created_at FROM batch_jobs WHERE id=$1", job_id)

    return BatchJobResponse(
        job_id=job_id,
        job_type="documents",
        status="pending",
        total_items=len(payload.documents),
        created_at=created_at,
    )


@router.get(
    "/documents/{job_id}/status",
    response_model=BatchJobStatusResponse,
    dependencies=[Depends(require_permission("PERM_RADI"))],
)
async def get_batch_status(
    job_id: UUID,
    request: Request,
    conn: asyncpg.Connection = Depends(get_db),
) -> BatchJobStatusResponse:
    """
    Get status of a batch job.

    Returns progress: total_items, processed_items, failed_items.
    Also includes details of each item (success/failed, result_id, error).

    **Status codes**:
    - `pending` — Queued, not started
    - `processing` — Currently processing
    - `completed` — All items succeeded
    - `failed` — Some or all items failed
    """
    tenant_slug = request.headers.get("X-Tenant-Slug", "")

    if not tenant_slug:
        raise HTTPException(status_code=400, detail="Missing tenant context")

    service = BatchService(conn, tenant_slug)
    job = await service.get_job_status(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    return BatchJobStatusResponse(**job)
