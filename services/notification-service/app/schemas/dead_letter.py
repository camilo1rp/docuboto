# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Esquemas Pydantic v2 para la inspección/replay del dead-letter (ADR-021 Fase 5 Inc.2).

El resumen (``DeadLetterSummary``) deliberadamente NO expone ``payload``,
``tracking_number`` ni ``last_error``: revelar el identificador del radicado o el
error concreto filtraría la existencia (y potencialmente el nivel) de objetos
clasificados a un admin que aún no superó el gate de clearance del detalle.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints


class DeadLetterSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    event_id: str
    event_type: str | None = None
    origin_stream: str | None = None
    origin_group: str | None = None
    status: str
    dead_lettered_at: datetime
    resolved_at: datetime | None = None
    # NOTA: sin payload, sin tracking_number, sin last_error — no filtra la
    # existencia de objetos clasificados a través del resumen.


class DeadLetterDetail(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    event_id: str
    event_type: str | None = None
    correlation_id: str | None = None
    tenant_slug: str
    origin_stream: str | None = None
    origin_group: str | None = None
    status: str
    dead_lettered_at: datetime
    resolved_at: datetime | None = None
    payload: dict  # sobre íntegro (JSONB)
    failure: dict  # failure_meta (JSONB)


class DeadLetterActionResult(BaseModel):
    event_id: str
    previous_status: str
    new_status: str
    outcome: dict  # p. ej. {"rows_affected": 1, "notifications_sent": 1}


class DiscardRequest(BaseModel):
    # FIX 4: min_length subido de 1 a 10 y strip_whitespace=True (aplica el
    # strip ANTES de contar longitud, pydantic v2) para exigir un motivo
    # sustantivo y trazable — no un carácter suelto — coherente con la
    # auditoría posterior exigida por MoReq / Ley 594 art. 23.
    reason: Annotated[str, StringConstraints(min_length=10, strip_whitespace=True)]
