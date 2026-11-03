# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas for workflow rules engine (E05)."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, model_validator

_OPS = Literal["eq", "ne", "contains", "not_contains", "in", "gt", "lt", "gte", "lte", "regex"]
_FIELDS = Literal[
    "doc_type",
    "doc_class",
    "dest_dept",
    "origin_dept",
    "dest_dept_code",
    "origin_dept_code",
    "subject",
    "sender_name",
    "sender_entity",
    "pages",
]


class RuleCondition(BaseModel):
    field: _FIELDS
    op: _OPS
    value: Any


class WorkflowRuleCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = None
    is_active: bool = True
    priority: int = Field(default=100, ge=1, le=999)
    # Condiciones nuevas (operadores + AND/OR). Si está vacío, se usan las
    # columnas legadas de abajo.
    conditions: list[RuleCondition] = Field(default_factory=list)
    match_mode: Literal["all", "any"] = "all"
    # Condiciones legadas (compatibilidad).
    doc_type: str | None = Field(None, description="E, S, or I")
    doc_class: str | None = None
    #: Condición legada de destino: por CÓDIGO (`dest_dept_code`); `dest_dept` es el
    #: ALIAS deprecado, se resuelve a código (ADR-032).
    dest_dept: str | None = None
    dest_dept_code: int | None = Field(default=None, ge=1, le=999999)
    subject_contains: str | None = None
    # Action: la dependencia destino es un CÓDIGO; `assign_to_dept` es el alias.
    assign_to_dept_code: int | None = Field(default=None, ge=1, le=999999)
    assign_to_dept: str | None = Field(default=None, min_length=1, max_length=200)
    assign_notes: str | None = None

    @model_validator(mode="after")
    def _destino_obligatorio(self) -> "WorkflowRuleCreate":
        if self.assign_to_dept_code is None and self.assign_to_dept is None:
            raise ValueError("Indique assign_to_dept_code (o, deprecado, assign_to_dept)")
        return self


class WorkflowRuleUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    is_active: bool | None = None
    priority: int | None = Field(None, ge=1, le=999)
    conditions: list[RuleCondition] | None = None
    match_mode: Literal["all", "any"] | None = None
    doc_type: str | None = None
    doc_class: str | None = None
    dest_dept: str | None = None
    dest_dept_code: int | None = Field(default=None, ge=1, le=999999)
    subject_contains: str | None = None
    assign_to_dept_code: int | None = Field(default=None, ge=1, le=999999)
    assign_to_dept: str | None = None
    assign_notes: str | None = None


class WorkflowRuleResponse(BaseModel):
    id: UUID
    name: str
    description: str | None
    is_active: bool
    priority: int
    conditions: list[RuleCondition] = Field(default_factory=list)
    match_mode: str = "all"
    doc_type: str | None
    doc_class: str | None
    dest_dept: str | None
    dest_dept_code: int | None = None
    subject_contains: str | None
    assign_to_dept: str
    assign_to_dept_code: int | None = None
    assign_notes: str | None
    created_by: UUID
    created_at: datetime
    updated_at: datetime


class RuleEvaluationRequest(BaseModel):
    """Contexto de un radicado para evaluar reglas y hallar la dependencia."""

    radicado_id: UUID
    tracking_number: str
    doc_type: str
    doc_class: str | None = None
    dest_dept: str | None = None
    origin_dept: str | None = None
    #: Códigos del radicado (ADR-032). Un evento anterior puede traer solo el nombre:
    #: el reparto lo resuelve con el historial y, si no es único, no casa por código.
    dest_dept_code: int | None = None
    origin_dept_code: int | None = None
    subject: str | None = None
    sender_name: str | None = None
    sender_entity: str | None = None
    pages: int | None = None


class RuleEvaluationResponse(BaseModel):
    matched: bool
    radicado_id: UUID | None = None
    rule_id: UUID | None = None
    rule_name: str | None = None
    assign_to_dept: str | None = None
    assign_to_dept_code: int | None = None
    assign_notes: str | None = None


class RuleBatchEvaluationRequest(BaseModel):
    items: list[RuleEvaluationRequest] = Field(..., min_length=1, max_length=500)


class RuleBatchEvaluationResponse(BaseModel):
    results: list[RuleEvaluationResponse]
