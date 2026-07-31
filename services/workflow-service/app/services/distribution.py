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

from orpycamcp_common import audit

from app.repositories.workflow_repository import WorkflowRepository
from app.schemas.rules import RuleEvaluationRequest
from app.schemas.workflow import FlowStepCreate
from app.services.rules_service import WorkflowRulesService
from app.services.workflow_service import WorkflowService

logger = logging.getLogger(__name__)

# Asignación automática del sistema (sin usuario humano).
SYSTEM_USER = uuid.UUID("00000000-0000-0000-0000-000000000000")

_SERVICE = "workflow-service"
_INVALID_DEPT_ACTION = "workflow.regla_dependencia_invalida"


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

    # `WorkflowService.assign` ahora valida `to_dept` contra el catálogo
    # `dependencias` (cierre del agujero de disponibilidad: un `to_dept` que no
    # corresponde a ninguna dependencia activa deja el radicado invisible en
    # toda bandeja). `workflow_rules.assign_to_dept` es el MISMO texto libre sin
    # FK que `flow_steps.to_dept` (`app/schemas/rules.py`) — no se valida al
    # crear/editar la regla (fuera de este cierre), así que una regla vieja, con
    # una errata, o que apunta a una dependencia luego renombrada/dada de baja
    # (deuda documentada en CLAUDE.md) haría que `assign` lance 422 aquí.
    #
    # Se comprueba ANTES de llamar a `assign` y se resuelve en NO-OP (mismo
    # resultado que "sin regla aplicable": el radicado queda en la bandeja del
    # radicador) en vez de dejar que la excepción se propague — un 422 fail-
    # closed tiene sentido en la escritura HTTP directa (hay un cliente al que
    # devolvérselo), pero aquí el "cliente" es el worker de eventos: propagar
    # dejaría el mensaje en la PEL, reintentando contra un dato permanentemente
    # inválido (no un fallo transitorio) hasta terminar en dead-letter en cada
    # reintento — ruido de DLQ garantizado por una regla mal configurada, no una
    # falla real del sistema. Se registra en logs para que quien administra las
    # reglas lo detecte y corrija la regla (fast-follow: alertar/auditar esto
    # explícitamente queda fuera de este cierre).
    if not await repo.dependencia_activa(result.assign_to_dept):
        logger.warning(
            "Regla '%s' (%s) apunta a la dependencia '%s', que no está activa en "
            "el catálogo — radicado %s NO se autodistribuye (queda en la bandeja "
            "del radicador). Corrija la regla o reactive la dependencia.",
            result.rule_name,
            result.rule_id,
            result.assign_to_dept,
            tracking_number,
        )
        # Visibilidad más allá del log (segunda mitad del residual declarado en
        # CLAUDE.md, cerrado 2026-07-30): un `logger.warning` que nadie mira no
        # es una traza — se deja además un asiento en Canal A
        # (`public.audit_log`) para que la desviación sea auditable sin
        # depender de que alguien revise los logs del worker.
        #
        # Best-effort a propósito, mismo criterio que
        # `WorkflowService._audit_bandeja_access`/`_audit_hoja_ruta_access`: si
        # `audit.append` falla, NO debe escalar a "reintentar el mensaje" — eso
        # convertiría un fallo transitorio de AUDITORÍA en ruido de DLQ contra
        # un dato de negocio (la regla) que sigue siendo permanentemente
        # inválido, exactamente el resultado que la degradación de arriba ya
        # decidió evitar. Tampoco se envuelve en `conn.transaction()`: esta
        # rama NO revierte nada (nunca se llega a `assign`, no hay mutación de
        # negocio con la que deba ser atómica) — es un evento de configuración
        # inválida detectado en ejecución, no una mutación, así que no aplica
        # el patrón de "auditoría dentro de la misma tx" que sí usan
        # `rollback`/`complete_step`/`devolver`/`execute_transaction`.
        try:
            await audit.append(
                conn,
                tenant_slug=tenant_slug,
                service=_SERVICE,
                action=_INVALID_DEPT_ACTION,
                object_type="radicado",
                object_ref=tracking_number,
                actor="system",
                canal="system",
                payload={
                    "rule_id": str(result.rule_id) if result.rule_id else None,
                    "rule_name": result.rule_name,
                    "assign_to_dept": result.assign_to_dept,
                },
            )
        except Exception:  # noqa: BLE001 — best-effort, ver docstring arriba.
            logger.warning(
                "audit.append (%s) failed", _INVALID_DEPT_ACTION, exc_info=True
            )
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
