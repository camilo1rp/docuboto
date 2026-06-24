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

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status

from app.core.database import get_db_conn
from app.schemas.workflow import (
    CascadeReassignRequest,
    CascadeReassignResponse,
    DevolucionRequest,
    FlowEventResponse,
    FlowHistoryResponse,
    FlowStepComplete,
    FlowStepCreate,
    FlowStepResponse,
    TransactionRequest,
    TransactionTypeResponse,
)
from app.services.workflow_service import (
    VALID_BOXES,
    RollbackError,
    TransactionError,
    WorkflowService,
)

router = APIRouter(prefix="/api/v1/workflows", tags=["workflows"])


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


def _require_user(x_user_id: str | None = Header(default=None)) -> uuid.UUID:
    if not x_user_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "missing_user",
                "detail": "X-User-Id header is required",
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
                "detail": "X-User-Id must be a valid UUID",
                "status": 400,
            },
        )


# NOTE: rutas literales antes de /{radicado_id}/... para que FastAPI no intente
# casar "pending"/"reassign" como UUID.
@router.post(
    "/reassign/cascade",
    response_model=CascadeReassignResponse,
    summary="Reassign all active steps of a user/dept to a new responsible (RF-FLU-08)",
)
async def reassign_cascade(
    payload: CascadeReassignRequest,
    tenant_slug: str = Depends(_require_tenant),
    actor: uuid.UUID = Depends(_require_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> CascadeReassignResponse:
    service = WorkflowService(conn, tenant_slug)
    count = await service.reassign_cascade(
        from_assigned_to=payload.from_assigned_to,
        from_dept=payload.from_dept,
        to_assigned_to=payload.to_assigned_to,
        actor=actor,
    )
    return CascadeReassignResponse(reassigned=count)


@router.get(
    "/transaction-types",
    response_model=list[TransactionTypeResponse],
    summary="Catalog of trámite transaction types (RF-FLU-01)",
)
async def list_transaction_types(
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[TransactionTypeResponse]:
    return await WorkflowService(conn, tenant_slug).list_transaction_types()


@router.post(
    "/overdue/scan",
    summary="Scan overdue steps and emit alerts to E16 (RF-FLU-07)",
)
async def scan_overdue(
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> dict[str, int]:
    """Barrido de vencimientos del tenant (idempotente por paso). Pensado para que
    un scheduler externo lo invoque periódicamente. Devuelve cuántos se alertaron."""
    service = WorkflowService(conn, tenant_slug)
    return {"alerted": await service.scan_overdue()}


@router.get(
    "/inbox",
    response_model=list[FlowStepResponse],
    summary="Typed inbox (entrada/salida/internos) of active steps (RF-FLU-03)",
)
async def get_inbox(
    response: Response,
    box: str | None = Query(default=None, description="entrada | salida | internos"),
    dept: str | None = Query(default=None),
    assigned_to: uuid.UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[FlowStepResponse]:
    if box is not None and box not in VALID_BOXES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_box",
                "detail": f"box debe ser uno de: {', '.join(sorted(VALID_BOXES))}",
                "status": 400,
            },
        )
    service = WorkflowService(conn, tenant_slug)
    items, total = await service.inbox(
        box=box, dept=dept, assigned_to=assigned_to, page=page, size=size
    )
    response.headers["X-Total-Count"] = str(total)
    return items


# NOTE: /pending must be declared before /{radicado_id}/... so FastAPI does not
# attempt to match the literal "pending" as a UUID path parameter.
@router.get(
    "/pending",
    response_model=list[FlowStepResponse],
    summary="List pending flow steps",
)
async def list_pending(
    response: Response,
    dept: str | None = Query(default=None),
    assigned_to: uuid.UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[FlowStepResponse]:
    service = WorkflowService(conn, tenant_slug)
    items, total = await service.list_pending(
        dept=dept, assigned_to=assigned_to, page=page, size=size
    )
    response.headers["X-Total-Count"] = str(total)
    return items


@router.post(
    "/assign",
    response_model=FlowStepResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Assign a radicado to a dependencia",
)
async def assign_radicado(
    payload: FlowStepCreate,
    tenant_slug: str = Depends(_require_tenant),
    assigned_by: uuid.UUID = Depends(_require_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> FlowStepResponse:
    service = WorkflowService(conn, tenant_slug)
    return await service.assign(payload, assigned_by)


@router.post(
    "/transfer",
    response_model=FlowStepResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Transfer a radicado to another dependencia",
)
async def transfer_radicado(
    payload: FlowStepCreate,
    tenant_slug: str = Depends(_require_tenant),
    assigned_by: uuid.UUID = Depends(_require_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> FlowStepResponse:
    service = WorkflowService(conn, tenant_slug)
    return await service.transfer(payload, assigned_by)


@router.get(
    "/{radicado_id}/history",
    response_model=FlowHistoryResponse,
    summary="Full flow history for a radicado",
)
async def get_flow_history(
    radicado_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> FlowHistoryResponse:
    service = WorkflowService(conn, tenant_slug)
    return await service.get_history(radicado_id)


@router.post(
    "/{radicado_id}/transactions",
    response_model=FlowEventResponse,
    summary="Execute a trámite transaction on a radicado (RF-FLU-01)",
)
async def execute_transaction(
    radicado_id: uuid.UUID,
    payload: TransactionRequest,
    tenant_slug: str = Depends(_require_tenant),
    actor: uuid.UUID = Depends(_require_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> FlowEventResponse:
    service = WorkflowService(conn, tenant_slug)
    try:
        return await service.execute_transaction(radicado_id, payload, actor)
    except TransactionError as exc:
        code = exc.code
        st = (
            status.HTTP_404_NOT_FOUND
            if code in ("unknown_transaction", "no_flow")
            else status.HTTP_400_BAD_REQUEST
        )
        raise HTTPException(
            status_code=st,
            detail={"error": code, "detail": "No se pudo ejecutar la transacción", "status": st},
        )


@router.post(
    "/{radicado_id}/devolver",
    response_model=FlowStepResponse,
    summary="Devolver el radicado al remitente/origen con causal (RF-FLU)",
)
async def devolver_radicado(
    radicado_id: uuid.UUID,
    payload: DevolucionRequest,
    tenant_slug: str = Depends(_require_tenant),
    actor: uuid.UUID = Depends(_require_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> FlowStepResponse:
    return await WorkflowService(conn, tenant_slug).devolver(
        radicado_id,
        to_dept=payload.to_dept,
        causal=payload.causal,
        comentario=payload.comentario,
        actor=actor,
    )


@router.post(
    "/{radicado_id}/rollback",
    response_model=FlowStepResponse,
    summary="Roll back the last assignment of a radicado (RF-FLU-08)",
)
async def rollback_radicado(
    radicado_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    actor: uuid.UUID = Depends(_require_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> FlowStepResponse:
    service = WorkflowService(conn, tenant_slug)
    try:
        return await service.rollback(radicado_id, actor)
    except RollbackError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"error": exc.code, "detail": "No se puede revertir", "status": 409},
        )


@router.get(
    "/{radicado_id}/events",
    response_model=list[FlowEventResponse],
    summary="Append-only flow event history for a radicado (RF-FLU-02)",
)
async def get_flow_events(
    radicado_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    actor: uuid.UUID = Depends(_require_user),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> list[FlowEventResponse]:
    # Hoja de ruta operativa: acotada por el clearance del llamante (E08 §9 / RF-SEG-08),
    # NO restringida a administrador. El X-User-Id ya lo exige _require_user.
    service = WorkflowService(conn, tenant_slug)
    return await service.get_events(radicado_id, actor)


@router.get(
    "/{radicado_id}/current",
    response_model=FlowStepResponse,
    summary="Current active flow step for a radicado",
)
async def get_current_step(
    radicado_id: uuid.UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> FlowStepResponse:
    service = WorkflowService(conn, tenant_slug)
    return await service.get_current(radicado_id)


@router.patch(
    "/{step_id}/complete",
    response_model=FlowStepResponse,
    summary="Mark a flow step as completed",
)
async def complete_step(
    step_id: uuid.UUID,
    payload: FlowStepComplete,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> FlowStepResponse:
    service = WorkflowService(conn, tenant_slug)
    return await service.complete_step(step_id, payload)
