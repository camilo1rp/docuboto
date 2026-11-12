# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""AREA-10-07: la DLQ compartida guarda el aparcamiento de CADA grupo (Postgres REAL).

`evento_dead_letter` es UNA tabla física que escriben workflow, signature,
notification y knowledge en el mismo schema de tenant. Con PK solo por `event_id`,
cuando un mismo evento agotaba los reintentos en dos grupos el segundo INSERT era un
no-op silencioso: ese grupo no dejaba fila, ni asiento, y su admin no lo veía.

Carril de integración: `RUN_INTEGRATION=1`; base inalcanzable = fallo ruidoso.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator
from urllib.parse import urlsplit

import asyncpg
import pytest
import pytest_asyncio

from tests.integration.conftest import drop_tenant_schema, provision_tenant_schema
from notification.repositories.dead_letter_repository import DeadLetterRepository

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("RUN_INTEGRATION") != "1", reason="requiere RUN_INTEGRATION=1"),
]

GRUPO = "notification-service"
OTRO_GRUPO = "workflow-service"
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
    schema = f"tenant_itest_{uuid.uuid4().hex[:8]}"
    try:
        await provision_tenant_schema(c, schema)
        await c.execute(f'SET search_path TO "{schema}"')
        yield c
    finally:
        await drop_tenant_schema(c, schema)
        await c.close()


async def _pk(c: asyncpg.Connection) -> list[str]:
    return [
        r["attname"]
        for r in await c.fetch(
            "SELECT a.attname FROM pg_constraint k "
            "CROSS JOIN LATERAL unnest(k.conkey) WITH ORDINALITY u(attnum, ord) "
            "JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = u.attnum "
            "WHERE k.conrelid = to_regclass('evento_dead_letter') AND k.contype = 'p' "
            "ORDER BY u.ord"
        )
    ]


async def _insertar(repo: DeadLetterRepository, eid: str, grupo: str):
    return await repo.insert_if_new(
        event_id=eid,
        event_type="document.radicado.created",
        correlation_id=None,
        tenant_slug="itest",
        payload_json="{}",
        failure_json="{}",
        origin_stream="orpycamcp.document.events",
        origin_group=grupo,
    )


@pytest.mark.asyncio
async def test_dos_grupos_aparcan_el_mismo_evento_y_cada_uno_deja_su_fila(conn) -> None:
    repo = DeadLetterRepository(conn)
    eid = f"evt-{uuid.uuid4().hex[:8]}"

    assert await _insertar(repo, eid, OTRO_GRUPO) is not None  # el otro consumidor, antes
    assert await _insertar(repo, eid, GRUPO) is not None, (
        "el aparcamiento de este grupo se descartó en silencio por un event_id ya usado"
    )
    grupos = {r["origin_group"] for r in await conn.fetch(
        "SELECT origin_group FROM evento_dead_letter WHERE event_id = $1", eid)}
    assert grupos == {GRUPO, OTRO_GRUPO}


@pytest.mark.asyncio
async def test_repetir_el_mismo_grupo_y_evento_sigue_siendo_idempotente(conn) -> None:
    repo = DeadLetterRepository(conn)
    eid = f"evt-{uuid.uuid4().hex[:8]}"
    assert await _insertar(repo, eid, GRUPO) is not None
    assert await _insertar(repo, eid, GRUPO) is None
    assert await conn.fetchval(
        "SELECT count(*) FROM evento_dead_letter WHERE event_id = $1", eid) == 1


@pytest.mark.asyncio
async def test_la_pk_es_por_grupo_y_evento(conn) -> None:
    assert await _pk(conn) == ["origin_group", "event_id"]


@pytest.mark.asyncio
async def test_la_ruta_del_worker_deja_un_asiento_por_grupo_y_no_repite(conn, monkeypatch) -> None:
    """Revisión de testing-engineer (T9): el repositorio aislado no prueba que el worker,
    que decide el asiento según lo que devuelve `insert_if_new`, deje DOS asientos
    `evento.dead_letter` cuando dos grupos aparcan el mismo evento, ni que repetir el
    mismo grupo no añada uno. Se ejercita `_persist_dead_letter` entera (Postgres real)."""
    from contextlib import asynccontextmanager

    from notification.workers import stream_worker as worker


    @asynccontextmanager
    async def _acquire(_slug: str):
        yield conn

    monkeypatch.setattr(worker, "acquire_tenant", _acquire)
    eid = f"evt-{uuid.uuid4().hex[:8]}"
    envelope = {
        "event_id": eid,
        "event_type": "document.radicado.created",
        "tenant_slug": "itest",
        "payload": {"id": "doc-1", "tracking_number": "2026-ITEST-E-000001"},
    }

    def _meta(grupo: str) -> dict:
        return {
            "dead_letter_group": grupo,
            "dead_letter_origin_stream": "orpycamcp.document.events",
            "dead_letter_last_error": "boom",
        }

    async def _asientos() -> int:
        return await conn.fetchval(
            "SELECT count(*) FROM public.audit_log "
            "WHERE object_ref = $1 AND action = 'evento.dead_letter'",
            eid,
        )

    await worker._persist_dead_letter(envelope, _meta(OTRO_GRUPO))
    await worker._persist_dead_letter(envelope, _meta(GRUPO))
    assert await _asientos() == 2, "el segundo grupo no dejó asiento: trazabilidad perdida"
    await worker._persist_dead_letter(envelope, _meta(GRUPO))  # redelivery del mismo grupo
    assert await _asientos() == 2
    assert await conn.fetchval(
        "SELECT count(*) FROM evento_dead_letter WHERE event_id = $1", eid
    ) == 2
