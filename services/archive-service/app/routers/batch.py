# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Batch API endpoints for bulk expediente operations."""

from __future__ import annotations

import asyncio
import logging
import uuid

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, status

from app.core.clearance import require_permission
from app.core.database import acquire_tenant
from app.core.database import get_db_conn as get_db
from app.schemas.batch import (
    BatchExpedientesRequest,
    BatchJobResponse,
    BatchJobStatusResponse,
)
from app.services.batch_service import BatchArchiveService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/batch", tags=["batch"])


def _require_tenant(x_tenant_slug: str | None = Header(default=None)) -> str:
    if not x_tenant_slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "missing_tenant",
                "detail": "Falta la cabecera X-Tenant-Slug",
                "status": 400,
            },
        )
    return x_tenant_slug


def _require_user(x_user_id: str | None = Header(default=None)) -> uuid.UUID:
    if not x_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "missing_user",
                "detail": "Falta la cabecera X-User-Id",
                "status": 400,
            },
        )
    try:
        return uuid.UUID(x_user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_user_id",
                "detail": "La cabecera X-User-Id debe ser un UUID válido",
                "status": 400,
            },
        )


_tareas_en_curso: set[asyncio.Task] = set()


async def _run_batch_job(tenant_slug: str, job_id: uuid.UUID) -> None:
    """Detached background task: owns its own connection, independent of the
    request's — see `app.core.database.acquire_tenant` for why the request-scoped
    connection from `Depends(get_db)` must never be reused here.
    """
    try:
        async with acquire_tenant(tenant_slug) as conn:
            service = BatchArchiveService(conn, tenant_slug)
            await service.process_batch_expedientes(job_id)
    except Exception as exc:  # noqa: BLE001 — nadie más puede recoger esto
        logger.exception("Batch job %s failed outside the request/response cycle", job_id)
        # El log solo lo ve quien administra el servidor. Quien lanzó el lote
        # mira una pantalla que sondea `batch_jobs`, y ahí un job que reventó y
        # uno que sigue trabajando eran indistinguibles: los dos se quedaban en
        # «pendiente» sin fecha de fin. Dejar constancia del fallo en el propio
        # job es lo que convierte esa espera indefinida en una respuesta.
        await _marcar_job_fallido(tenant_slug, job_id, str(exc))


async def _marcar_job_fallido(tenant_slug: str, job_id: uuid.UUID, motivo: str) -> None:
    """Marca el job como fallido, en una conexión nueva.

    Nueva a propósito: si se llega hasta aquí, la anterior es justamente la que
    pudo haber quedado inservible.
    """
    try:
        async with acquire_tenant(tenant_slug) as conn:
            await conn.execute(
                """
                UPDATE batch_jobs
                SET status = 'failed', completed_at = now(), error_message = $2
                WHERE id = $1
                """,
                job_id,
                motivo[:1000],
            )
    except Exception:  # noqa: BLE001
        logger.exception("No se pudo marcar como fallido el lote %s", job_id)


@router.post(
    "/expedientes",
    response_model=BatchJobResponse,
    status_code=202,
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def create_batch_expedientes(
    payload: BatchExpedientesRequest,
    tenant_slug: str = Depends(_require_tenant),
    user_id: uuid.UUID = Depends(_require_user),
    conn: asyncpg.Connection = Depends(get_db),
) -> BatchJobResponse:
    """
    Create a batch job for multiple expedientes (1-100).

    Each expediente can include radicado_ids to link on creation.
    Returns 202 Accepted with job_id — processing is asynchronous.
    """
    service = BatchArchiveService(conn, tenant_slug)
    job_id = await service.create_batch_expedientes(payload, user_id)

    # La referencia se retiene: el event loop solo guarda una débil, así que sin
    # esto el recolector puede llevarse un lote a medio procesar en silencio.
    tarea = asyncio.create_task(_run_batch_job(tenant_slug, job_id))
    _tareas_en_curso.add(tarea)
    tarea.add_done_callback(_tareas_en_curso.discard)

    # Fetch created_at from DB
    created_at = await conn.fetchval("SELECT created_at FROM batch_jobs WHERE id=$1", job_id)

    return BatchJobResponse(
        job_id=job_id,
        job_type="expedientes",
        status="pending",
        total_items=len(payload.expedientes),
        created_at=created_at,
    )


@router.get(
    "/expedientes/{job_id}/status",
    response_model=BatchJobStatusResponse,
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def get_batch_status(
    job_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db),
) -> BatchJobStatusResponse:
    """
    Get status of a batch job (documents or expedientes).

    - `pending` — queued, not started yet
    - `processing` — currently running
    - `completed` — all items succeeded
    - `failed` — one or more items failed (others may have succeeded)
    """
    service = BatchArchiveService(conn, tenant_slug)
    job = await service.get_job_status(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="No existe el trabajo solicitado")

    return BatchJobStatusResponse(**job)
