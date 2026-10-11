# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La bandeja respeta el no-read-up en las FILAS y en el TOTAL, contra Postgres REAL (AREA-13-01).

`test_bandeja_rf_seg_08.py` (mockeado) solo comprueba el TEXTO del SQL. Lo que un
mock no puede ver es la semántica: que el predicado entre en el `WHERE` que
comparten el `COUNT(*)` y la página (`X-Total-Count` no debe delatar filas
ocultas), y que un paso cuyo radicado no tiene fila en `radicados` no se lea como
PUBLICO (decisión ya fijada en `test_huerfano_no_es_publico.py`; aquí se fija en
la bandeja, que es por donde se enumera).

Mutaciones que este test detecta y el mockeado no: borrar
`conditions.append(_clearance_condition(...))` de `list_pending`/`list_inbox`
(el total pasa a 2 o 3), cambiar el `COALESCE(..., 3)` a 1 o invertir la
correlación del subselect (el huérfano o el reservado se cuelan).
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.core.database import tenant_schema
from app.repositories.workflow_repository import WorkflowRepository

pytestmark = pytest.mark.integration


async def _radicado(conn: asyncpg.Connection, nivel: int) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO radicados (tracking_number, nivel_seguridad) VALUES ($1, $2) RETURNING id",
        f"2026-IT-E-{uuid.uuid4().hex[:6]}",
        nivel,
    )


async def _paso(conn: asyncpg.Connection, rid: uuid.UUID) -> None:
    await conn.execute(
        "INSERT INTO flow_steps (radicado_id, tracking_number, step_number, from_dept, "
        "to_dept, to_dept_code, assigned_by, action, status) "
        "VALUES ($1, 'T', 1, 'A', 'B', 7, $2, 'assign', 'pending')",
        rid,
        uuid.uuid4(),
    )


@pytest.fixture
async def bandeja(tenant_ctx):
    """Un radicado publico (1), uno reservado (3) y un paso HUERFANO (sin radicado)."""
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", public')
    publico = await _radicado(conn, 1)
    reservado = await _radicado(conn, 3)
    huerfano = uuid.uuid4()  # flow_step sin fila en `radicados` (p. ej. tras purga TRD)
    for rid in (publico, reservado, huerfano):
        await _paso(conn, rid)
    return WorkflowRepository(conn, slug), publico, reservado, huerfano


async def _pending(repo: WorkflowRepository, clearance: int):
    return await repo.list_pending(
        dept_code=7,  # alcance ampliado: el eje del clearance es el que se prueba, no el de la dependencia
        assigned_to=None,
        own_scope_user_id=None,
        max_clearance=clearance,
        offset=0,
        limit=50,
    )


async def _inbox(repo: WorkflowRepository, clearance: int):
    return await repo.list_inbox(
        doc_type=None,
        dept_code=7,  # alcance ampliado: el eje del clearance es el que se prueba, no el de la dependencia
        assigned_to=None,
        own_scope_user_id=None,
        max_clearance=clearance,
        offset=0,
        limit=50,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("consulta", [_pending, _inbox])
async def test_clearance_1_ve_solo_el_publico_y_el_total_no_delata(bandeja, consulta) -> None:
    repo, publico, reservado, huerfano = bandeja
    filas, total = await consulta(repo, 1)
    ids = {f.radicado_id for f in filas}
    assert ids == {publico}, "clearance 1 solo ve el radicado publico"
    assert total == 1, "X-Total-Count no puede contar el reservado ni el huerfano"
    assert reservado not in ids and huerfano not in ids


@pytest.mark.asyncio
@pytest.mark.parametrize("consulta", [_pending, _inbox])
async def test_control_positivo_clearance_3_ve_el_reservado(bandeja, consulta) -> None:
    """Sin este control, un predicado que filtra TODO pasaria el test de arriba."""
    repo, publico, reservado, huerfano = bandeja
    filas, total = await consulta(repo, 3)
    ids = {f.radicado_id for f in filas}
    assert {publico, reservado} <= ids
    assert total == len(filas) >= 2
