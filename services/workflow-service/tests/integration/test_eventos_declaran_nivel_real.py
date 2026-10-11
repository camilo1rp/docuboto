# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: AGPL v3 o posterior. Se entrega SIN GARANTÍA ALGUNA.

"""OMI-01 contra Postgres REAL: los TRES productores de workflow declaran el nivel.

Las pruebas con mock parchean `nivel_radicado` y por tanto no prueban que la
consulta real resuelva (el `search_path` en el momento de emitir, la tabla, el
tipo). Aquí se emite con la base de verdad y se inspecciona el sobre que saldría
al stream. Un productor que no lo declare saca el evento «mínimo» por el webhook
(fail-closed, pero inservible): por eso se prueba el cable, no solo la pieza.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from app.core.database import tenant_schema
from app.repositories.workflow_repository import WorkflowRepository
from app.services.workflow_service import WorkflowService

pytestmark = pytest.mark.integration


async def _radicado(conn: asyncpg.Connection, nivel: int) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO radicados (tracking_number, nivel_seguridad) VALUES ($1, $2) RETURNING id",
        f"2026-IT-E-{uuid.uuid4().hex[:6]}",
        nivel,
    )


@pytest.fixture
async def ctx(tenant_ctx):
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    emit = AsyncMock()
    with (
        patch("app.services.workflow_service.events.emit", emit),
        patch("app.services.workflow_service.get_redis", return_value=MagicMock()),
    ):
        yield WorkflowService(conn, slug), conn, emit


def _niveles(emit: AsyncMock) -> list[object]:
    assert emit.await_count >= 1, "no se emitió nada: la prueba no mide el sobre"
    return [c.kwargs["payload"].get("nivel_seguridad", "AUSENTE") for c in emit.await_args_list]


@pytest.mark.asyncio
async def test_nivel_radicado_resuelve_y_un_ausente_es_none(ctx) -> None:
    svc, conn, _ = ctx
    rid = await _radicado(conn, 3)
    repo = WorkflowRepository(conn, svc._tenant_slug)
    assert await repo.nivel_radicado(rid) == 3
    assert await repo.nivel_radicado(uuid.uuid4()) is None


@pytest.mark.asyncio
async def test_publish_declara_el_nivel_real(ctx) -> None:
    svc, conn, emit = ctx
    rid = await _radicado(conn, 3)
    step = SimpleNamespace(
        id=uuid.uuid4(), radicado_id=rid, tracking_number="T", step_number=1,
        from_dept="A", to_dept="B", from_dept_code=1, to_dept_code=2, action="assign",
        status="pending", assigned_by=None,
    )
    await svc._publish("workflow.step.created", step)
    assert _niveles(emit) == [3]


@pytest.mark.asyncio
async def test_emitir_cambios_declara_el_nivel_real(ctx) -> None:
    svc, conn, emit = ctx
    rid = await _radicado(conn, 2)
    svc._cambios = [
        SimpleNamespace(
            radicado_id=rid, tracking_number="T", antes="registered",
            despues="in_progress", flow_event_id=uuid.uuid4(),
        )
    ]
    await svc._emitir_cambios(None)
    assert _niveles(emit) == [2]


@pytest.mark.asyncio
async def test_scan_overdue_declara_el_nivel_real(ctx) -> None:
    svc, conn, emit = ctx
    rid = await _radicado(conn, 3)
    await conn.execute(
        "INSERT INTO flow_steps (radicado_id, tracking_number, step_number, from_dept, "
        "to_dept, to_dept_code, assigned_by, action, status, due_at) "
        "VALUES ($1, 'T', 1, 'A', 'B', 7, $2, 'assign', 'pending', $3)",
        rid, uuid.uuid4(), datetime.now(tz=UTC) - timedelta(days=1),
    )
    assert await svc.scan_overdue() == 1
    assert _niveles(emit) == [3]


@pytest.mark.asyncio
async def test_un_radicado_purgado_no_declara_nivel_fail_closed(ctx) -> None:
    """Sin fila no hay nivel que inventar: se omite y notification lo trata como reservado."""
    svc, conn, emit = ctx
    step = SimpleNamespace(
        id=uuid.uuid4(), radicado_id=uuid.uuid4(), tracking_number="T", step_number=1,
        from_dept="A", to_dept="B", from_dept_code=1, to_dept_code=2, action="assign",
        status="pending", assigned_by=None,
    )
    await svc._publish("workflow.step.created", step)
    assert _niveles(emit) == ["AUSENTE"]
