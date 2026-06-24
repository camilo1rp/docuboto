# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Workflow Rules Engine endpoints (E05)."""

from uuid import UUID

import asyncpg
from fastapi import APIRouter, Depends, Header, HTTPException, Response, status

from app.core.database import get_db_conn as get_db
from app.schemas.rules import (
    RuleBatchEvaluationRequest,
    RuleBatchEvaluationResponse,
    RuleEvaluationRequest,
    RuleEvaluationResponse,
    WorkflowRuleCreate,
    WorkflowRuleResponse,
    WorkflowRuleUpdate,
)
from app.services.rules_service import WorkflowRulesService

router = APIRouter(prefix="/api/v1/workflow/rules", tags=["workflow-rules"])

_NIL_USER = UUID("00000000-0000-0000-0000-000000000000")


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


def _user(x_user_id: str | None = Header(default=None)) -> UUID:
    if not x_user_id:
        return _NIL_USER
    try:
        return UUID(x_user_id)
    except ValueError:
        return _NIL_USER


@router.post("", response_model=WorkflowRuleResponse, status_code=201)
async def create_rule(
    payload: WorkflowRuleCreate,
    tenant_slug: str = Depends(_require_tenant),
    user_id: UUID = Depends(_user),
    conn: asyncpg.Connection = Depends(get_db),
) -> WorkflowRuleResponse:
    """Crea una regla de enrutamiento. Condiciones con operadores (`conditions`)
    combinadas por `match_mode` (all/any), o columnas legadas. Menor `priority` gana."""
    svc = WorkflowRulesService(conn, tenant_slug)
    return await svc.create_rule(payload, user_id)


@router.get("", response_model=list[WorkflowRuleResponse])
async def list_rules(
    active_only: bool = False,
    page: int = 1,
    size: int = 50,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db),
) -> list[WorkflowRuleResponse]:
    """Lista las reglas ordenadas por prioridad."""
    svc = WorkflowRulesService(conn, tenant_slug)
    rules, _ = await svc.list_rules(only_active=active_only, page=page, size=size)
    return rules


@router.post("/evaluate", response_model=RuleEvaluationResponse)
async def evaluate_rules(
    payload: RuleEvaluationRequest,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db),
) -> RuleEvaluationResponse:
    """Evalúa las reglas activas contra un radicado y devuelve la primera que casa."""
    svc = WorkflowRulesService(conn, tenant_slug)
    return await svc.evaluate(payload)


@router.post("/evaluate/batch", response_model=RuleBatchEvaluationResponse)
async def evaluate_rules_batch(
    payload: RuleBatchEvaluationRequest,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db),
) -> RuleBatchEvaluationResponse:
    """Evalúa varios radicados contra el mismo conjunto de reglas (1-500)."""
    svc = WorkflowRulesService(conn, tenant_slug)
    return await svc.evaluate_batch(payload)


@router.get("/{rule_id}", response_model=WorkflowRuleResponse)
async def get_rule(
    rule_id: UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db),
) -> WorkflowRuleResponse:
    """Obtiene una regla por ID."""
    svc = WorkflowRulesService(conn, tenant_slug)
    rule = await svc.get_rule(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule


@router.patch("/{rule_id}", response_model=WorkflowRuleResponse)
async def update_rule(
    rule_id: UUID,
    payload: WorkflowRuleUpdate,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db),
) -> WorkflowRuleResponse:
    """Actualiza una regla (parcial — solo los campos provistos)."""
    svc = WorkflowRulesService(conn, tenant_slug)
    rule = await svc.update_rule(rule_id, payload)
    if not rule:
        raise HTTPException(status_code=404, detail="Rule not found")
    return rule


@router.delete("/{rule_id}", status_code=204)
async def delete_rule(
    rule_id: UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db),
) -> Response:
    """Elimina una regla."""
    svc = WorkflowRulesService(conn, tenant_slug)
    deleted = await svc.delete_rule(rule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Rule not found")
    return Response(status_code=204)
