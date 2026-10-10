# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""AREA-02-06: la DLQ de una entidad no ve ni muta la de otra (Postgres REAL).

`evento_dead_letter` lleva `tenant_slug` y el aislamiento NO depende solo del
`search_path`: cada consulta del repositorio filtra por `tenant_slug = $n`. Es el
único punto de notification donde la frontera entre entidades es un predicado y
no un schema, y no tenía una sola prueba de cruce.

Carril de integración: `RUN_INTEGRATION=1`. Con él, una base inalcanzable falla
ruidoso (no se hace skip): el operador pidió integración.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from urllib.parse import urlsplit

import asyncpg
import pytest
import pytest_asyncio

from app.repositories.dead_letter_repository import DeadLetterRepository

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("RUN_INTEGRATION") != "1", reason="requiere RUN_INTEGRATION=1"),
]

GRUPO = "notification-service"
# Dos entidades = dos schemas; el gate califica `radicados` con el de la entidad que pregunta.
A = "itest-aa"
B = "itest-bb"
_MIGRACIONES = Path(__file__).resolve().parents[2] / "migrations" / "tenant"
# Allowlist fail-closed del host: este tier crea y destruye un schema.
_HOSTS = frozenset({"postgres", "localhost", "127.0.0.1", "db"})


def _dsn() -> str:
    raw = os.environ.get("MIGRATIONS_DATABASE_URL", "") or os.environ.get("DATABASE_URL", "")
    assert raw, "RUN_INTEGRATION=1 sin DATABASE_URL: configuración de entorno errónea"
    dsn = raw.replace("postgresql+asyncpg://", "postgresql://")
    host = urlsplit(dsn).hostname or ""
    assert host in _HOSTS, f"host {host!r} fuera de la allowlist del tier de integración"
    return dsn


@pytest_asyncio.fixture
async def conn() -> AsyncGenerator[asyncpg.Connection, None]:
    c = await asyncpg.connect(_dsn())
    schema = "tenant_itest_bb"
    schema_a = "tenant_itest_aa"
    await c.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
    await c.execute(f'DROP SCHEMA IF EXISTS "{schema_a}" CASCADE')
    await c.execute(f'CREATE SCHEMA "{schema}"')
    await c.execute(f'CREATE SCHEMA "{schema_a}"')
    radicados = (  # el gate de clearance de la DLQ lee `radicados` (document-service)
        "CREATE TABLE {}.radicados (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
        "tracking_number varchar(50) NOT NULL UNIQUE, nivel_seguridad smallint NOT NULL)"
    )
    try:
        # La DLQ física es una (en `bb`); `aa` solo aporta su propia `radicados`.
        await c.execute(f'SET search_path TO "{schema}", "{schema_a}", public')
        await c.execute(radicados.format(f'"{schema}"'))
        await c.execute(radicados.format(f'"{schema_a}"'))
        for nombre in (
            "005_evento_dead_letter.sql",
            "006_deadletter_status.sql",
            "007_deadletter_origin_group_notnull_index.sql",
            "010_dead_letter_pk_por_grupo.sql",  # el INSERT usa ON CONFLICT (origin_group, event_id)
        ):
            await c.execute((_MIGRACIONES / nombre).read_text())
        yield c
    finally:
        assert schema.startswith("tenant_itest_")
        await c.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await c.execute(f'DROP SCHEMA IF EXISTS "{schema_a}" CASCADE')
        await c.close()


async def _sembrar(repo: DeadLetterRepository, slug: str) -> str:
    eid = f"evt-{uuid.uuid4().hex[:8]}"
    ins = await repo.insert_if_new(
        event_id=eid,
        event_type="document.radicado.created",
        correlation_id=None,
        tenant_slug=slug,
        payload_json="{}",
        failure_json="{}",
        origin_stream="orpycamcp.document.events",
        origin_group=GRUPO,
    )
    assert ins is not None
    return eid


async def _estado(conn: asyncpg.Connection, eid: str) -> str:
    return await conn.fetchval("SELECT status FROM evento_dead_letter WHERE event_id = $1", eid)


@pytest.mark.asyncio
async def test_a_no_ve_ni_muta_la_dlq_de_b(conn: asyncpg.Connection) -> None:
    repo = DeadLetterRepository(conn)
    eid = await _sembrar(repo, B)

    # Control positivo: la entidad dueña sí lo ve.
    assert await repo.get_by_event_id(eid, B, GRUPO, 3) is not None

    # La entidad ajena: ni lo lee, ni lo bloquea, ni lo transiciona, ni lo lista.
    assert await repo.get_by_event_id(eid, A, GRUPO, 3) is None
    async with conn.transaction():
        assert await repo.get_for_update(eid, A, GRUPO, 3) is None
    assert await repo.transition_status(eid, A, "discarded", GRUPO) == 0
    assert await _estado(conn, eid) == "pending"
    filas_a, total_a = await repo.list_visible(A, GRUPO, 3)
    assert eid not in [r["event_id"] for r in filas_a] and total_a == 0
    filas_b, _ = await repo.list_visible(B, GRUPO, 3)
    assert eid in [r["event_id"] for r in filas_b]


@pytest.mark.asyncio
async def test_purga_de_a_no_borra_lo_terminal_de_b(conn: asyncpg.Connection) -> None:
    repo = DeadLetterRepository(conn)
    eid = await _sembrar(repo, B)
    assert await repo.transition_status(eid, B, "resolved", GRUPO) == 1
    await conn.execute(
        "UPDATE evento_dead_letter SET resolved_at = now() - interval '400 days' "
        "WHERE event_id = $1",
        eid,
    )

    assert await repo.purge_terminal(30, A, GRUPO) == 0
    assert await conn.fetchval("SELECT count(*) FROM evento_dead_letter WHERE event_id=$1", eid) == 1
    # Control positivo: con el slug dueño, la misma fila sí es purgable.
    assert await repo.purge_terminal(30, B, GRUPO) == 1
