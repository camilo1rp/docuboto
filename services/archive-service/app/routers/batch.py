# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Batch API endpoints for bulk expediente operations."""

import asyncio
from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request

from app.core.database import get_db_conn as get_db
from app.schemas.batch import (
    BatchExpedientesRequest,
    BatchJobResponse,
    BatchJobStatusResponse,
)
from app.services.batch_service import BatchArchiveService

router = APIRouter(prefix="/api/v1/batch", tags=["batch"])


@router.post("/expedientes", response_model=BatchJobResponse, status_code=202)
async def create_batch_expedientes(
    request: Request,
    payload: BatchExpedientesRequest,
    conn: asyncpg.Connection = Depends(get_db),
) -> BatchJobResponse:
    """
    Create a batch job for multiple expedientes (1-100).

    Each expediente can include radicado_ids to link on creation.
    Returns 202 Accepted with job_id — processing is asynchronous.
    """
    tenant_slug = request.state.auth_headers.get("X-Tenant-Slug", "")
    user_id = request.state.auth_headers.get("X-User-Id", "")

    if not tenant_slug or not user_id:
        raise HTTPException(status_code=400, detail="Missing tenant or user context")

    service = BatchArchiveService(conn, tenant_slug)
    job_id = await service.create_batch_expedientes(payload, UUID(user_id))

    asyncio.create_task(service.process_batch_expedientes(job_id))

    # Fetch created_at from DB
    created_at = await conn.fetchval("SELECT created_at FROM batch_jobs WHERE id=$1", job_id)

    return BatchJobResponse(
        job_id=job_id,
        job_type="expedientes",
        status="pending",
        total_items=len(payload.expedientes),
        created_at=created_at,
    )


@router.get("/{job_id}/status", response_model=BatchJobStatusResponse)
async def get_batch_status(
    job_id: UUID,
    request: Request,
    conn: asyncpg.Connection = Depends(get_db),
) -> BatchJobStatusResponse:
    """
    Get status of a batch job (documents or expedientes).

    - `pending` — queued, not started yet
    - `processing` — currently running
    - `completed` — all items succeeded
    - `failed` — one or more items failed (others may have succeeded)
    """
    tenant_slug = request.state.auth_headers.get("X-Tenant-Slug", "")
    if not tenant_slug:
        raise HTTPException(status_code=400, detail="Missing tenant context")

    service = BatchArchiveService(conn, tenant_slug)
    job = await service.get_job_status(job_id)

    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    return BatchJobStatusResponse(**job)
