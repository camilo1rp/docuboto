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
from dataclasses import dataclass
from datetime import datetime


@dataclass
class FlowStep:
    id: uuid.UUID
    radicado_id: uuid.UUID
    tracking_number: str
    step_number: int
    from_dept: str | None
    to_dept: str
    assigned_to: uuid.UUID | None
    assigned_by: uuid.UUID
    action: str
    notes: str | None
    status: str
    created_at: datetime
    completed_at: datetime | None
    doc_type: str | None = None
    due_at: datetime | None = None
    overdue_notified: bool = False
    #: Llave de correlación (ADR-032): `dependencias.codigo`. `to_dept`/`from_dept` son
    #: la instantánea del NOMBRE al momento del acto, no se unen a nada.
    to_dept_code: int | None = None
    from_dept_code: int | None = None


@dataclass
class FlowAssignment:
    """Intermediate value object used during step creation."""

    radicado_id: uuid.UUID
    tracking_number: str
    to_dept: str
    assigned_to: uuid.UUID | None
    assigned_by: uuid.UUID
    action: str
    notes: str | None
    from_dept: str | None
    to_dept_code: int | None = None
    from_dept_code: int | None = None
