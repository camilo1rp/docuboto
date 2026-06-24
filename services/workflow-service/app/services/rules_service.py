# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Workflow Rules Engine — gestión y evaluación de reglas de enrutamiento (E05)."""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

import asyncpg

from app.schemas.rules import (
    RuleBatchEvaluationRequest,
    RuleBatchEvaluationResponse,
    RuleEvaluationRequest,
    RuleEvaluationResponse,
    WorkflowRuleCreate,
    WorkflowRuleResponse,
    WorkflowRuleUpdate,
)
from app.services.rules_engine import rule_matches


def _conditions(raw: Any) -> list[dict[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    return json.loads(raw)


def _to_response(row: asyncpg.Record) -> WorkflowRuleResponse:
    data = dict(row)
    data["conditions"] = _conditions(data.get("conditions"))
    return WorkflowRuleResponse(**data)


def _context(req: RuleEvaluationRequest) -> dict[str, Any]:
    return {
        "doc_type": req.doc_type,
        "doc_class": req.doc_class,
        "dest_dept": req.dest_dept,
        "origin_dept": req.origin_dept,
        "subject": req.subject,
        "sender_name": req.sender_name,
        "sender_entity": req.sender_entity,
        "pages": req.pages,
    }


class WorkflowRulesService:
    def __init__(self, conn: asyncpg.Connection, tenant_slug: str) -> None:
        self.conn = conn
        self.tenant_slug = tenant_slug

    async def _set_search_path(self) -> None:
        # Las tablas de reglas viven en tenant_{slug} (ADR-012).
        await self.conn.execute(f"SET search_path TO tenant_{self.tenant_slug}, public")

    async def create_rule(
        self, payload: WorkflowRuleCreate, created_by: UUID
    ) -> WorkflowRuleResponse:
        await self._set_search_path()
        row = await self.conn.fetchrow(
            """
            INSERT INTO workflow_rules
                (name, description, is_active, priority, conditions, match_mode,
                 doc_type, doc_class, dest_dept, subject_contains,
                 assign_to_dept, assign_notes, created_by)
            VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9,$10,$11,$12,$13)
            RETURNING *
            """,
            payload.name,
            payload.description,
            payload.is_active,
            payload.priority,
            json.dumps([c.model_dump() for c in payload.conditions]),
            payload.match_mode,
            payload.doc_type,
            payload.doc_class,
            payload.dest_dept,
            payload.subject_contains,
            payload.assign_to_dept,
            payload.assign_notes,
            created_by,
        )
        return _to_response(row)

    async def list_rules(
        self, *, only_active: bool = False, page: int = 1, size: int = 50
    ) -> tuple[list[WorkflowRuleResponse], int]:
        await self._set_search_path()
        where = "WHERE is_active = true" if only_active else ""
        offset = (page - 1) * size

        rows = await self.conn.fetch(
            f"SELECT * FROM workflow_rules {where} "
            "ORDER BY priority, created_at LIMIT $1 OFFSET $2",
            size,
            offset,
        )
        total: int = await self.conn.fetchval(f"SELECT COUNT(*) FROM workflow_rules {where}") or 0
        return [_to_response(r) for r in rows], total

    async def get_rule(self, rule_id: UUID) -> WorkflowRuleResponse | None:
        await self._set_search_path()
        row = await self.conn.fetchrow("SELECT * FROM workflow_rules WHERE id=$1", rule_id)
        return _to_response(row) if row else None

    async def update_rule(
        self, rule_id: UUID, payload: WorkflowRuleUpdate
    ) -> WorkflowRuleResponse | None:
        await self._set_search_path()
        data = payload.model_dump(exclude_unset=True)
        if not data:
            return await self.get_rule(rule_id)

        set_parts: list[str] = []
        values: list[Any] = []
        idx = 2
        for col, val in data.items():
            if col == "conditions":
                set_parts.append(f"conditions = ${idx}::jsonb")
                values.append(json.dumps(val))
            else:
                set_parts.append(f"{col} = ${idx}")
                values.append(val)
            idx += 1

        row = await self.conn.fetchrow(
            f"""
            UPDATE workflow_rules
            SET {', '.join(set_parts)}, updated_at=now()
            WHERE id=$1
            RETURNING *
            """,
            rule_id,
            *values,
        )
        return _to_response(row) if row else None

    async def delete_rule(self, rule_id: UUID) -> bool:
        await self._set_search_path()
        result = await self.conn.execute("DELETE FROM workflow_rules WHERE id=$1", rule_id)
        return result == "DELETE 1"

    # ------------------------------------------------------------------
    # Evaluación
    # ------------------------------------------------------------------

    async def _active_rules(self) -> list[asyncpg.Record]:
        await self._set_search_path()
        return list(
            await self.conn.fetch(
                "SELECT * FROM workflow_rules WHERE is_active = true "
                "ORDER BY priority, created_at"
            )
        )

    async def _log_activation(self, rule_id: UUID, radicado_id: UUID, tracking_number: str) -> None:
        await self.conn.execute(
            """
            INSERT INTO workflow_rule_activations
                (rule_id, radicado_id, tracking_number)
            VALUES ($1, $2, $3)
            """,
            rule_id,
            radicado_id,
            tracking_number,
        )

    def _first_match(
        self, rules: list[asyncpg.Record], req: RuleEvaluationRequest
    ) -> asyncpg.Record | None:
        context = _context(req)
        for row in rules:
            if rule_matches(dict(row), _conditions(row["conditions"]), context):
                return row
        return None

    async def evaluate(self, request: RuleEvaluationRequest) -> RuleEvaluationResponse:
        """Evalúa las reglas activas (por prioridad) y devuelve la primera que casa."""
        rules = await self._active_rules()
        match = self._first_match(rules, request)
        if match is None:
            return RuleEvaluationResponse(matched=False, radicado_id=request.radicado_id)

        await self._log_activation(match["id"], request.radicado_id, request.tracking_number)
        return RuleEvaluationResponse(
            matched=True,
            radicado_id=request.radicado_id,
            rule_id=match["id"],
            rule_name=match["name"],
            assign_to_dept=match["assign_to_dept"],
            assign_notes=match["assign_notes"],
        )

    async def evaluate_batch(
        self, request: RuleBatchEvaluationRequest
    ) -> RuleBatchEvaluationResponse:
        """Evalúa varios radicados contra el mismo conjunto de reglas (una sola
        lectura de reglas)."""
        rules = await self._active_rules()
        results: list[RuleEvaluationResponse] = []
        for item in request.items:
            match = self._first_match(rules, item)
            if match is None:
                results.append(RuleEvaluationResponse(matched=False, radicado_id=item.radicado_id))
                continue
            await self._log_activation(match["id"], item.radicado_id, item.tracking_number)
            results.append(
                RuleEvaluationResponse(
                    matched=True,
                    radicado_id=item.radicado_id,
                    rule_id=match["id"],
                    rule_name=match["name"],
                    assign_to_dept=match["assign_to_dept"],
                    assign_notes=match["assign_notes"],
                )
            )
        return RuleBatchEvaluationResponse(results=results)
