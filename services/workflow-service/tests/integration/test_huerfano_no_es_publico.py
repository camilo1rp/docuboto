# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""AREA-10-03 / AREA-01-N1 / AREA-01-06 contra Postgres REAL.

Sin FK de `flow_steps`/`flow_events` a `radicados`, la purga TRD deja huérfanos
append-only. `radicados.nivel_seguridad` es NOT NULL: una fila AUSENTE no
significa «sin clasificar». Antes el COALESCE la leía como PUBLICA y la hoja de
ruta de un RESERVADO borrado quedaba legible para clearance 1.

Y la cadena de vistos buenos hereda el no-read-up del radicado.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.core.database import tenant_schema
from app.repositories.visto_bueno_repository import VistoBuenoRepository
from app.repositories.workflow_repository import WorkflowRepository

pytestmark = pytest.mark.integration


async def _radicado(conn: asyncpg.Connection, nivel: int) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO radicados (tracking_number, nivel_seguridad) "
        "VALUES ($1, $2) RETURNING id",
        f"2026-IT-E-{uuid.uuid4().hex[:6]}",
        nivel,
    )


async def _flow(conn: asyncpg.Connection, rid: uuid.UUID) -> None:
    await conn.execute(
        "INSERT INTO flow_events (radicado_id, tracking_number, tipo_tx, from_dept, "
        "to_dept, comentario) VALUES ($1, 'T', 'asignar', 'A', 'B', 'secreto')",
        rid,
    )
    await conn.execute(
        "INSERT INTO flow_steps (radicado_id, tracking_number, step_number, from_dept, "
        "to_dept, assigned_by, action, status) "
        "VALUES ($1, 'T', 1, 'A', 'B', $2, 'assign', 'pending')",
        rid,
        uuid.uuid4(),
    )


@pytest.fixture
async def ctx(tenant_ctx):
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    return slug, conn


@pytest.mark.asyncio
async def test_huerfano_de_un_reservado_no_es_publico(ctx) -> None:
    slug, conn = ctx
    repo = WorkflowRepository(conn, slug)
    rid = await _radicado(conn, 3)
    await _flow(conn, rid)

    # Control positivo: con el radicado vivo, clearance 3 lo ve y clearance 1 no.
    assert await repo.list_flow_events(rid, max_clearance=3)
    assert await repo.list_flow_events(rid, max_clearance=1) == []
    assert await repo.get_current(rid, max_clearance=3) is not None

    # Purga TRD: se borra la fila de radicados; los huérfanos quedan.
    await conn.execute("DELETE FROM radicados WHERE id = $1", rid)
    assert await conn.fetchval("SELECT count(*) FROM flow_events WHERE radicado_id = $1", rid) == 1

    assert await repo.list_flow_events(rid, max_clearance=1) == []
    assert await repo.get_history(rid, max_clearance=1) == []
    assert await repo.get_current(rid, max_clearance=1) is None
    pend, _ = await repo.list_pending(
        dept=None, assigned_to=None, own_scope_user_id=None, max_clearance=1, offset=0, limit=50
    )
    assert rid not in [p.radicado_id for p in pend]


@pytest.mark.asyncio
async def test_cadena_de_vobo_hereda_el_no_read_up(ctx) -> None:
    slug, conn = ctx
    repo = VistoBuenoRepository(conn, slug)
    rev = uuid.uuid4()
    rid = await _radicado(conn, 3)
    await repo.crear_cadena(rid, [rev])
    await conn.execute("UPDATE visto_bueno SET comentario = 'reservado' WHERE radicado_id = $1", rid)

    # Control positivo.
    assert len(await repo.list(rid, 3)) == 1
    assert await repo.active(rid, 3) is not None
    # Clearance 1: ni la lista ni el turno activo.
    assert await repo.list(rid, 1) == []
    assert await repo.active(rid, 1) is None
    assert not await repo.radicado_visible(rid, 1)
    assert await repo.radicado_visible(rid, 3)
