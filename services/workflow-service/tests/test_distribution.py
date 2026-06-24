# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de distribución automática por reglas (E05, RF-FLU-04)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.distribution import auto_distribute
from tests.conftest import RADICADO_ID, TRACKING_NUMBER, make_step_record

NOW = datetime.now(tz=UTC)


def _conn() -> AsyncMock:
    conn = AsyncMock()
    conn.execute.return_value = None
    conn.transaction = MagicMock(return_value=AsyncMock())
    return conn


def _rule_row(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "name": "Entradas a Jurídica",
        "description": None,
        "is_active": True,
        "priority": 100,
        "conditions": json.dumps([{"field": "doc_type", "op": "eq", "value": "E"}]),
        "match_mode": "all",
        "doc_type": None,
        "doc_class": None,
        "dest_dept": None,
        "subject_contains": None,
        "assign_to_dept": "Dirección Jurídica",
        "assign_notes": "auto",
        "created_by": uuid.uuid4(),
        "created_at": NOW,
        "updated_at": NOW,
    }
    base.update(kw)
    return base


_PAYLOAD = {
    "id": str(RADICADO_ID),
    "tracking_number": TRACKING_NUMBER,
    "doc_type": "E",
    "subject": "Solicitud",
    "dest_dept": "Ventanilla",
}


@pytest.mark.asyncio
async def test_auto_distribute_assigns_on_match() -> None:
    conn = _conn()
    conn.fetchval.return_value = 1  # get_next_step_number (sin pasos previos)
    conn.fetch.return_value = [_rule_row()]  # reglas activas
    conn.fetchrow.return_value = make_step_record(action="assign")  # INSERT paso

    dept = await auto_distribute(conn, "demo", _PAYLOAD)
    assert dept == "Dirección Jurídica"


@pytest.mark.asyncio
async def test_auto_distribute_idempotent_when_already_has_steps() -> None:
    conn = _conn()
    conn.fetchval.return_value = 2  # ya tiene pasos → no redistribuir

    dept = await auto_distribute(conn, "demo", _PAYLOAD)
    assert dept is None


@pytest.mark.asyncio
async def test_auto_distribute_no_rule_match() -> None:
    conn = _conn()
    conn.fetchval.return_value = 1
    conn.fetch.return_value = [
        _rule_row(conditions=json.dumps([{"field": "doc_type", "op": "eq", "value": "S"}]))
    ]

    dept = await auto_distribute(conn, "demo", _PAYLOAD)
    assert dept is None
