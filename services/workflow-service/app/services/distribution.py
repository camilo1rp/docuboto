# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Distribución automática por enrutador (E05, RF-FLU-04).

Consume `document.radicado.created`: evalúa las reglas activas sobre el radicado
y, si alguna casa, lo autoasigna a la dependencia de la regla (paso + evento
append-only). Idempotente: si el radicado ya tiene pasos, no redistribuye.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import asyncpg

from app.repositories.workflow_repository import WorkflowRepository
from app.schemas.rules import RuleEvaluationRequest
from app.schemas.workflow import FlowStepCreate
from app.services.rules_service import WorkflowRulesService
from app.services.workflow_service import WorkflowService

logger = logging.getLogger(__name__)

# Asignación automática del sistema (sin usuario humano).
SYSTEM_USER = uuid.UUID("00000000-0000-0000-0000-000000000000")


async def auto_distribute(
    conn: asyncpg.Connection, tenant_slug: str, payload: dict[str, Any]
) -> str | None:
    """Autoasigna un radicado según las reglas. Devuelve la dependencia asignada,
    o None si no había regla aplicable o ya estaba distribuido."""
    radicado_id = uuid.UUID(str(payload["id"]))
    tracking_number = payload["tracking_number"]

    repo = WorkflowRepository(conn, tenant_slug)
    # Idempotencia (RN-8): un radicado ya con pasos no se redistribuye.
    if await repo.get_next_step_number(radicado_id) > 1:
        return None

    req = RuleEvaluationRequest(
        radicado_id=radicado_id,
        tracking_number=tracking_number,
        doc_type=payload["doc_type"],
        doc_class=payload.get("doc_class"),
        dest_dept=payload.get("dest_dept"),
        origin_dept=payload.get("origin_dept"),
        subject=payload.get("subject"),
        sender_name=payload.get("sender_name"),
        sender_entity=payload.get("sender_entity"),
        pages=payload.get("pages"),
    )
    result = await WorkflowRulesService(conn, tenant_slug).evaluate(req)
    if not result.matched or not result.assign_to_dept:
        # Sin regla: queda en la bandeja del radicador (no-op aquí).
        return None

    await WorkflowService(conn, tenant_slug).assign(
        FlowStepCreate(
            radicado_id=radicado_id,
            tracking_number=tracking_number,
            to_dept=result.assign_to_dept,
            action="assign",
            notes=result.assign_notes,
            doc_type=payload.get("doc_type"),
            due_at=payload.get("due_date"),
        ),
        assigned_by=SYSTEM_USER,
    )
    logger.info(
        "Radicado %s autoasignado a '%s' por la regla '%s'",
        tracking_number,
        result.assign_to_dept,
        result.rule_name,
    )
    return result.assign_to_dept
