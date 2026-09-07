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
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status

from app.core.clearance import require_permission
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


def _user(x_user_id: str | None = Header(default=None)) -> UUID:
    """Identidad del creador de la regla. Fail-closed (400) — antes degradaba en
    silencio a un `_NIL_USER` fijo cuando faltaba/era inválida la cabecera, lo
    que dejaba la autoría de `created_by` como un UUID nulo indistinguible de un
    usuario real. Ahora que `create_rule` está gateado por `require_permission`
    (que ya exige `X-User-Id` para resolver el RBAC), el gateway SIEMPRE inyecta
    la cabecera antes de llegar aquí — este 400 es inalcanzable en producción, no
    rompe al llamante legítimo. Gemelo de `_require_user` en `workflow.py`."""
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
        return UUID(x_user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "invalid_user_id",
                "detail": "La cabecera X-User-Id debe ser un UUID válido",
                "status": 400,
            },
        )


@router.post(
    "",
    response_model=WorkflowRuleResponse,
    status_code=201,
    # Reglas de enrutamiento automático = administración (deciden a qué
    # dependencia se asigna la correspondencia entrante) → USUA_PERM_ADMIN, no
    # una operación de trámite propio. Gateado por endpoint, no por router
    # (aunque hoy TODAS las rutas de este router —incluidas /evaluate y
    # /evaluate/batch, Hallazgo B— comparten el mismo gate; se deja explícito
    # por endpoint para que una futura ruta solo-lectura no gateada no herede
    # el permiso por accidente ni pierda el gate por accidente).
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)
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


@router.get(
    "",
    response_model=list[WorkflowRuleResponse],
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)
async def list_rules(
    active_only: bool = False,
    # `page` y `size` sin cota dejaban pedir una página de cualquier tamaño: el
    # resto de listados del sistema los acota con `Query(ge=..., le=...)` y éste
    # se había quedado fuera. Una consulta barata no puede pedir trabajo caro.
    page: int = Query(default=1, ge=1),
    size: int = Query(default=50, ge=1, le=200),
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db),
) -> list[WorkflowRuleResponse]:
    """Lista las reglas ordenadas por prioridad."""
    svc = WorkflowRulesService(conn, tenant_slug)
    rules, _ = await svc.list_rules(only_active=active_only, page=page, size=size)
    return rules


@router.post(
    "/evaluate",
    response_model=RuleEvaluationResponse,
    # Hallazgo B (cerrado): oráculo del CRUD de reglas. `GET /workflow/rules`
    # está gateado con USUA_PERM_ADMIN precisamente porque la tabla de
    # enrutamiento es sensible (decide a qué dependencia va cada radicado
    # entrante) — pero /evaluate devolvía qué regla casa para cualquier
    # metadato sintético, así que iterando inputs se reconstruía esa misma
    # tabla sin el permiso. Verificado (auditor + humano): NINGÚN llamante
    # HTTP real existe hoy — el motor de distribución
    # (`app/workers/document_consumer.py` → `app/services/distribution.py`)
    # instancia `WorkflowRulesService` DIRECTAMENTE en proceso, nunca por HTTP
    # — así que gatear esta ruta no rompe la distribución automática.
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)
async def evaluate_rules(
    payload: RuleEvaluationRequest,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db),
) -> RuleEvaluationResponse:
    """Evalúa las reglas activas contra un radicado y devuelve la primera que casa.

    Solo-lectura (no muta reglas), pero gateada como el resto del CRUD porque
    permite reconstruir la tabla de enrutamiento completa por fuerza bruta de
    metadatos (Hallazgo B). Si en el futuro un servicio externo empieza a
    invocarla por HTTP, migrar a `X-Internal-Token`
    (`document-service/app/core/internal_auth.py`) en vez de RBAC de usuario."""
    svc = WorkflowRulesService(conn, tenant_slug)
    return await svc.evaluate(payload)


@router.post(
    "/evaluate/batch",
    response_model=RuleBatchEvaluationResponse,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)
async def evaluate_rules_batch(
    payload: RuleBatchEvaluationRequest,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db),
) -> RuleBatchEvaluationResponse:
    """Evalúa varios radicados contra el mismo conjunto de reglas (1-500).

    Mismo estado que `evaluate_rules` arriba: además, sin gate, 500 ítems por
    petición eran una amplificación barata del mismo oráculo (Hallazgo B).
    Ver docstring de `evaluate_rules`."""
    svc = WorkflowRulesService(conn, tenant_slug)
    return await svc.evaluate_batch(payload)


@router.get(
    "/{rule_id}",
    response_model=WorkflowRuleResponse,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)
async def get_rule(
    rule_id: UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db),
) -> WorkflowRuleResponse:
    """Obtiene una regla por ID."""
    svc = WorkflowRulesService(conn, tenant_slug)
    rule = await svc.get_rule(rule_id)
    if not rule:
        raise HTTPException(status_code=404, detail="No existe la regla")
    return rule


@router.patch(
    "/{rule_id}",
    response_model=WorkflowRuleResponse,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)
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
        raise HTTPException(status_code=404, detail="No existe la regla")
    return rule


@router.delete(
    "/{rule_id}",
    status_code=204,
    dependencies=[Depends(require_permission("USUA_PERM_ADMIN"))],
)
async def delete_rule(
    rule_id: UUID,
    tenant_slug: str = Depends(_require_tenant),
    conn: asyncpg.Connection = Depends(get_db),
) -> Response:
    """Elimina una regla."""
    svc = WorkflowRulesService(conn, tenant_slug)
    deleted = await svc.delete_rule(rule_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="No existe la regla")
    return Response(status_code=204)
