# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from __future__ import annotations

import uuid
from datetime import date

import asyncpg
from fastapi import (
    APIRouter,
    Depends,
    Header,
    HTTPException,
    Query,
    Request,
    Response,
    status,
)

from app.core.clearance import check_permission, require_permission
from app.core.database import get_db_conn
from app.repositories.archive_repository import ArchiveRepository
from app.schemas.archive import (
    BatchLinkRequest,
    BatchLinkResult,
    ExpedienteCreate,
    ExpedienteEventoResponse,
    ExpedienteIndice,
    ExpedienteRadicadoResponse,
    ExpedienteResponse,
    ExpedienteSearchHit,
    ExpedienteSearchResponse,
    ExpedienteUpdate,
    RadicadoLink,
)
from app.services.archive_service import ArchiveService
from app.services.metadata_service import MetadataValidationError

router = APIRouter(prefix="/api/v1/expedientes", tags=["expedientes"])


def _require_tenant(x_tenant_slug: str | None = Header(default=None)) -> str:
    if not x_tenant_slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "missing_tenant",
                "detail": "X-Tenant-Slug header is required",
                "status": 400,
            },
        )
    return x_tenant_slug


def _optional_user(x_user_id: str | None = Header(default=None)) -> uuid.UUID | None:
    if not x_user_id:
        return None
    try:
        return uuid.UUID(x_user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_user_id",
                "detail": "X-User-Id must be a valid UUID",
                "status": 400,
            },
        )


@router.post(
    "",
    response_model=ExpedienteResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an expediente",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def create_expediente(
    payload: ExpedienteCreate,
    tenant_slug: str = Depends(_require_tenant),
    opened_by: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> ExpedienteResponse:
    service = ArchiveService(conn, tenant_slug)
    try:
        return await service.create_expediente(payload, opened_by)
    except MetadataValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "metadata_validation_failed",
                "detail": str(exc),
                "status": 422,
            },
        )


@router.get(
    "",
    response_model=list[ExpedienteResponse],
    summary="List expedientes",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def list_expedientes(
    response: Response,
    exp_status: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[ExpedienteResponse]:
    service = ArchiveService(conn, tenant_slug)
    items, total = await service.list_expedientes(status=exp_status, page=page, size=size)
    response.headers["X-Total-Count"] = str(total)
    return items


@router.get(
    "/search",
    response_model=ExpedienteSearchResponse,
    summary="Full-text search of expedientes (E09, ADR-014)",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def search_expedientes(
    request: Request,
    response: Response,
    q: str | None = Query(default=None, min_length=2, description="Texto (opcional)"),
    exp_status: str | None = Query(default=None, alias="status"),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    tenant_slug: str = Depends(_require_tenant),
    user_id: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> ExpedienteSearchResponse:
    metadata_filter = {
        k[len("meta.") :]: v
        for k, v in request.query_params.items()
        if k.startswith("meta.") and k != "meta."
    }
    rows, total = await ArchiveRepository(conn, tenant_slug).search_expedientes(
        q=q,
        status=exp_status,
        date_from=date_from,
        date_to=date_to,
        metadata_filter=metadata_filter or None,
        page=page,
        size=size,
        user_id=user_id,
    )
    response.headers["X-Total-Count"] = str(total)
    return ExpedienteSearchResponse(
        query=q,
        total=total,
        hits=[ExpedienteSearchHit(**dict(r)) for r in rows],
        page=page,
        size=size,
    )


@router.post(
    "/{expediente_id}/close",
    response_model=ExpedienteResponse,
    summary="Close expediente and materialize TRD disposition (E02)",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE", min_crud=3))],
)
async def close_expediente(
    expediente_id: uuid.UUID,
    x_username: str | None = Header(default=None),
    x_user_id: str | None = Header(default=None),
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> ExpedienteResponse:
    firmante = x_user_id or x_username
    # `actor_id` es estrictamente X-User-Id (UUID de auth_users.id): es lo que
    # signature-service exige para sellar el índice (E06 Inc.1, RF-FIR-11/12).
    # Sin él la firma no se intenta y el índice queda 'pendiente_firma' — el
    # cierre no se bloquea (decisión del usuario, RT-16).
    return await ArchiveService(conn, tenant_slug).close_expediente(
        expediente_id, firmante, actor_id=x_user_id
    )


@router.post(
    "/{expediente_id}/transfer",
    response_model=ExpedienteResponse,
    summary="Transfer expediente (closed → transferred) (E02)",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE", min_crud=3))],
)
async def transfer_expediente(
    expediente_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> ExpedienteResponse:
    return await ArchiveService(conn, tenant_slug).transfer_expediente(expediente_id)


@router.get(
    "/{expediente_id}/foliado",
    response_model=ExpedienteIndice,
    summary="Expediente foliation (ordered radicados) (E02)",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def get_foliado(
    expediente_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    user_id: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> ExpedienteIndice:
    return await ArchiveService(conn, tenant_slug).get_indice(expediente_id, user_id)


@router.get(
    "/{expediente_id}",
    response_model=ExpedienteResponse,
    summary="Get expediente by ID (includes linked radicados)",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def get_expediente(
    expediente_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    user_id: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> ExpedienteResponse:
    service = ArchiveService(conn, tenant_slug)
    return await service.get_expediente(expediente_id, user_id)


@router.get(
    "/{expediente_id}/eventos",
    response_model=list[ExpedienteEventoResponse],
    summary="Hoja de ruta append-only del expediente (E02 §9, RF-EXP-09)",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def list_expediente_eventos(
    expediente_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    user_id: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[ExpedienteEventoResponse]:
    # Canal B / hoja de ruta operativa: acotada por la visibilidad del expediente para el
    # clearance del llamante (E08 §9 / RF-SEG-08); NO restringida a administrador.
    service = ArchiveService(conn, tenant_slug)
    return await service.list_eventos(expediente_id, user_id)


@router.patch(
    "/{expediente_id}",
    response_model=ExpedienteResponse,
    summary="Update expediente fields or transition status",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def update_expediente(
    expediente_id: uuid.UUID,
    payload: ExpedienteUpdate,
    tenant_slug: str = Depends(_require_tenant),
    actor: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> ExpedienteResponse:
    # El PATCH genérico puede transicionar el ciclo de vida (open→closed→
    # transferred), las MISMAS transiciones que /close y /transfer, que exigen
    # autoridad de disposición (crud>=3). Sin este gate condicional, el PATCH
    # sería una ruta alterna que evade el umbral reforzado (Ley 594/2000 arts.
    # 22-24; Acuerdo AGN 001/2024). El decorador exige USUA_PERM_EXPEDIENTE con
    # el default crud>=1 (basta para editar nombre/descr/serie); solo la
    # transición de disposición eleva a crud>=3. `require_permission` ya fijó el
    # search_path del tenant, así que `check_permission` consulta el schema correcto.
    if payload.status in ("closed", "transferred") and not await check_permission(
        conn, actor, "USUA_PERM_EXPEDIENTE", min_crud=3
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "forbidden",
                "detail": "La disposición del expediente (cierre/transferencia) "
                "requiere USUA_PERM_EXPEDIENTE con nivel crud>=3.",
            },
        )
    service = ArchiveService(conn, tenant_slug)
    return await service.update_expediente(expediente_id, payload)


@router.post(
    "/{expediente_id}/radicados",
    response_model=ExpedienteRadicadoResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Link a radicado to an expediente",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def link_radicado(
    expediente_id: uuid.UUID,
    payload: RadicadoLink,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> ExpedienteRadicadoResponse:
    service = ArchiveService(conn, tenant_slug)
    return await service.link_radicado(expediente_id, payload)


@router.post(
    "/{expediente_id}/radicados/batch",
    response_model=BatchLinkResult,
    summary="Inclusión masiva de radicados a un expediente (paridad legado)",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE"))],
)
async def link_radicados_batch(
    expediente_id: uuid.UUID,
    payload: BatchLinkRequest,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> BatchLinkResult:
    return await ArchiveService(conn, tenant_slug).link_radicados_batch(
        expediente_id, payload.radicados
    )


@router.delete(
    "/{expediente_id}/radicados/{radicado_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Excluir (soft-delete) un radicado del expediente",
    dependencies=[Depends(require_permission("USUA_PERM_EXPEDIENTE", min_crud=3))],
)
async def unlink_radicado(
    expediente_id: uuid.UUID,
    radicado_id: uuid.UUID,
    causal: str | None = Query(default=None, max_length=500),
    tenant_slug: str = Depends(_require_tenant),
    actor: uuid.UUID | None = Depends(_optional_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> None:
    # E15 perfil v3 (RT-15 art. 4.3.2.3): la exclusión es SOFT-DELETE — el
    # vínculo permanece marcado 'excluido' para que el índice muestre el
    # hueco trazable, en vez de un DELETE físico. `actor` (X-User-Id de quien
    # excluye) se propaga a la hoja de ruta + audit_log (trazabilidad del
    # agente responsable, Ley 594/2000; Ac. AGN 001/2024 art. 4.3.2.3).
    service = ArchiveService(conn, tenant_slug)
    await service.unlink_radicado(expediente_id, radicado_id, causal=causal, actor=actor)
