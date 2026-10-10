# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""AREA-10-08: el no-read-up de la DLQ vive en el WHERE (Postgres REAL).

Antes el listado traía hasta 1000 candidatas, resolvía su nivel en Python y paginaba en
memoria: con más de 1000 filas reservadas recientes, una fila pública antigua quedaba
fuera de la ventana y el total mentía. Ahora el nivel del radicado VIVO se evalúa en la
consulta, antes de paginar y de contar, y detalle/replay/descarte usan el mismo predicado.

Carril de integración: `RUN_INTEGRATION=1`; base inalcanzable = fallo ruidoso.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from urllib.parse import urlsplit

import asyncpg
import pytest
import pytest_asyncio
from orpycamcp_common.deadletter import RadicadosAusenteError

from app.repositories.dead_letter_repository import DeadLetterRepository

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(os.environ.get("RUN_INTEGRATION") != "1", reason="requiere RUN_INTEGRATION=1"),
]

GRUPO = "workflow-service"
OTRO_GRUPO = "signature-service"
TENANT = f"itest-{uuid.uuid4().hex[:8]}"  # su schema `tenant_itest_xxxxxxxx` es el que el gate califica
_MIGRACIONES = Path(__file__).resolve().parents[2] / "migrations" / "tenant"
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
    schema = "tenant_" + TENANT.replace("-", "_")
    await c.execute(f'CREATE SCHEMA "{schema}"')
    try:
        await c.execute(f'SET search_path TO "{schema}", public')
        # `radicados` es de document-service: solo las columnas que el gate lee.
        await c.execute(
            "CREATE TABLE radicados (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), "
            "tracking_number varchar(50) NOT NULL UNIQUE, nivel_seguridad smallint NOT NULL)"
        )
        # Las migraciones REALES del servicio que tocan la DLQ, en orden.
        for path in sorted(_MIGRACIONES.glob("*.sql")):
            texto = path.read_text()
            if "evento_dead_letter" in texto:
                await c.execute(texto)
        yield c
    finally:
        assert schema.startswith("tenant_itest_")
        await c.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
        await c.close()


async def _radicado(c: asyncpg.Connection, nivel: int) -> tuple[str, uuid.UUID]:
    tn = f"2026-ITEST-E-{uuid.uuid4().hex[:8]}"
    rid = await c.fetchval(
        "INSERT INTO radicados (tracking_number, nivel_seguridad) VALUES ($1, $2) RETURNING id",
        tn,
        nivel,
    )
    return tn, rid


async def _aparcar(
    c: asyncpg.Connection,
    inner: object,
    *,
    grupo: str = GRUPO,
    status: str = "pending",
    antiguedad_s: int = 0,
    doble_codificado: bool = False,
) -> str:
    eid = f"evt-{uuid.uuid4().hex[:10]}"
    sobre: object = {"event_id": eid, "tenant_slug": TENANT, "payload": inner}
    texto = json.dumps(sobre)
    if doble_codificado:
        texto = json.dumps(texto)
    await c.execute(
        "INSERT INTO evento_dead_letter (event_id, tenant_slug, payload, failure, origin_group, "
        "origin_stream, status, dead_lettered_at) "
        "VALUES ($1, $2, $3::jsonb, '{}'::jsonb, $4, 'orpycamcp.document.events', $5, "
        "now() - make_interval(secs => $6))",
        eid,
        TENANT,
        texto,
        grupo,
        status,
        antiguedad_s,
    )
    return eid


async def _ids(repo: DeadLetterRepository, clearance: int, **kw: object) -> list[str]:
    rows, _ = await repo.list_visible(TENANT, GRUPO, clearance, limit=100, **kw)  # type: ignore[arg-type]
    return [r["event_id"] for r in rows]


@pytest.mark.asyncio
async def test_mas_de_mil_reservadas_recientes_no_tapan_la_publica_antigua(conn) -> None:
    repo = DeadLetterRepository(conn)
    tn3, _ = await _radicado(conn, 3)
    tn1, _ = await _radicado(conn, 1)
    await conn.execute(
        "INSERT INTO evento_dead_letter (event_id, tenant_slug, payload, failure, origin_group, "
        "origin_stream, status, dead_lettered_at) "
        "SELECT 'r-' || g, $1, jsonb_build_object('payload', jsonb_build_object("
        "'tracking_number', $2::text)), '{}'::jsonb, $3, 's', 'pending', now() "
        "FROM generate_series(1, 1001) g",
        TENANT,
        tn3,
        GRUPO,
    )
    publica = await _aparcar(conn, {"tracking_number": tn1}, antiguedad_s=86400 * 30)

    rows, total = await repo.list_visible(TENANT, GRUPO, 1, limit=20)
    assert [r["event_id"] for r in rows] == [publica]
    assert total == 1
    _, total3 = await repo.list_visible(TENANT, GRUPO, 3, limit=20)
    assert total3 == 1002


@pytest.mark.asyncio
async def test_la_paginacion_y_el_total_cuentan_solo_lo_visible(conn) -> None:
    repo = DeadLetterRepository(conn)
    tn1, _ = await _radicado(conn, 1)
    tn3, _ = await _radicado(conn, 3)
    for i in range(25):
        await _aparcar(conn, {"tracking_number": tn1}, antiguedad_s=i)
    for i in range(30):
        await _aparcar(conn, {"tracking_number": tn3}, antiguedad_s=i)

    p1, total = await repo.list_visible(TENANT, GRUPO, 1, offset=0, limit=20)
    p2, total2 = await repo.list_visible(TENANT, GRUPO, 1, offset=20, limit=20)
    pasada, total3 = await repo.list_visible(TENANT, GRUPO, 1, offset=100, limit=20)
    assert (len(p1), total) == (20, 25)
    assert (len(p2), total2) == (5, 25)
    assert (pasada, total3) == ([], 25), "un offset pasado de fin no debe dar total 0"
    assert not ({r["event_id"] for r in p1} & {r["event_id"] for r in p2})


@pytest.mark.asyncio
async def test_reclasificado_hacia_arriba_tras_aparcar_se_oculta_en_todas_las_rutas(conn) -> None:
    repo = DeadLetterRepository(conn)
    tn, _ = await _radicado(conn, 1)
    eid = await _aparcar(conn, {"tracking_number": tn})
    assert await _ids(repo, 1) == [eid]

    await conn.execute("UPDATE radicados SET nivel_seguridad = 3 WHERE tracking_number = $1", tn)

    assert await _ids(repo, 1) == []
    ajeno = f"evt-{uuid.uuid4().hex[:10]}"
    oculto = await repo.get_by_event_id(eid, TENANT, GRUPO, 1)
    inexistente = await repo.get_by_event_id(ajeno, TENANT, GRUPO, 1)
    assert oculto is None and inexistente is None, "oculto e inexistente deben ser indistinguibles"
    async with conn.transaction():
        assert await repo.get_for_update(eid, TENANT, GRUPO, 1) is None
        assert await repo.get_for_update(ajeno, TENANT, GRUPO, 1) is None
        assert await repo.get_for_update(eid, TENANT, GRUPO, 3) is not None
    assert await repo.get_by_event_id(eid, TENANT, GRUPO, 3) is not None


@pytest.mark.asyncio
async def test_radicado_purgado_o_sin_identificadores_solo_lo_ve_clearance_3(conn) -> None:
    repo = DeadLetterRepository(conn)
    purgado = await _aparcar(conn, {"tracking_number": "2026-NO-EXISTE-E-000001"})
    sin_ids = await _aparcar(conn, {"asunto": "sin referencia"})
    for nivel in (1, 2):
        assert await _ids(repo, nivel) == []
    assert set(await _ids(repo, 3)) == {purgado, sin_ids}


@pytest.mark.asyncio
async def test_el_nivel_embebido_solo_puede_subir_y_la_duda_oculta(conn) -> None:
    repo = DeadLetterRepository(conn)
    tn, _ = await _radicado(conn, 1)
    control = await _aparcar(conn, {"tracking_number": tn, "nivel_nuevo": 1})
    await _aparcar(conn, {"tracking_number": tn, "nivel_nuevo": "3"})
    await _aparcar(conn, {"tracking_number": tn, "nivel_nuevo": 3})
    await _aparcar(conn, {"tracking_number": tn, "nivel_nuevo": "x"})
    await _aparcar(conn, {"tracking_number": tn, "nivel_nuevo": 2.0})
    await _aparcar(conn, {"tracking_number": tn, "nivel_nuevo": True})
    cae_a_nivel_seguridad = await _aparcar(
        conn, {"tracking_number": tn, "nivel_nuevo": None, "nivel_seguridad": 3}
    )
    sin_embebido = await _aparcar(conn, {"tracking_number": tn})
    visibles = set(await _ids(repo, 1))
    assert visibles == {control, sin_embebido}
    assert cae_a_nivel_seguridad not in visibles
    assert len(await _ids(repo, 3)) == 8


@pytest.mark.asyncio
async def test_sobre_malformado_no_rompe_el_listado_y_se_oculta(conn) -> None:
    repo = DeadLetterRepository(conn)
    abc = await _aparcar(conn, {"id": "abc"})
    num = await _aparcar(conn, {"id": 12345})
    doble = await _aparcar(conn, {"tracking_number": "x"}, doble_codificado=True)
    lista = await _aparcar(conn, [1, 2, 3])
    assert await _ids(repo, 2) == []
    assert set(await _ids(repo, 3)) == {abc, num, doble, lista}
    _, rid = await _radicado(conn, 1)
    por_id = await _aparcar(conn, {"id": str(rid).upper()})  # UUID válido, resuelve al vivo
    assert await _ids(repo, 1) == [por_id]


@pytest.mark.asyncio
async def test_otro_grupo_y_otros_estados_no_se_ven(conn) -> None:
    repo = DeadLetterRepository(conn)
    tn, _ = await _radicado(conn, 1)
    propio = await _aparcar(conn, {"tracking_number": tn})
    ajeno = await _aparcar(conn, {"tracking_number": tn}, grupo=OTRO_GRUPO)
    resuelto = await _aparcar(conn, {"tracking_number": tn}, status="resolved")
    assert await _ids(repo, 3) == [propio]
    assert await repo.get_by_event_id(ajeno, TENANT, GRUPO, 3) is None
    assert await _ids(repo, 3, status="resolved") == [resuelto]


@pytest.mark.asyncio
async def test_un_radicados_ajeno_en_el_search_path_no_se_lee(conn) -> None:
    """El gate califica `radicados` con el schema del tenant: si éste no la tiene, una
    homónima de otro schema del `search_path` (p. ej. `public`) NO la sustituye."""
    repo = DeadLetterRepository(conn)
    ajeno = f"decoy_{uuid.uuid4().hex[:8]}"
    await conn.execute(f'CREATE SCHEMA "{ajeno}"')
    try:
        await conn.execute(
            f'CREATE TABLE "{ajeno}".radicados (id uuid PRIMARY KEY DEFAULT gen_random_uuid(), '
            "tracking_number varchar(50) NOT NULL UNIQUE, nivel_seguridad smallint NOT NULL)"
        )
        await conn.execute(
            f"INSERT INTO \"{ajeno}\".radicados (tracking_number, nivel_seguridad) VALUES ('X-1', 1)"
        )
        await _aparcar(conn, {"tracking_number": "X-1"})
        await conn.execute("DROP TABLE radicados")
        schema = "tenant_" + TENANT.replace("-", "_")
        await conn.execute(f'SET search_path TO "{schema}", "{ajeno}", public')
        with pytest.raises(RadicadosAusenteError):
            await repo.list_visible(TENANT, GRUPO, 1)
    finally:
        await conn.execute(f'DROP SCHEMA IF EXISTS "{ajeno}" CASCADE')


@pytest.mark.asyncio
async def test_sin_tabla_radicados_es_error_explicito_no_lista_vacia(conn) -> None:
    repo = DeadLetterRepository(conn)
    eid = await _aparcar(conn, {"tracking_number": "x"})
    await conn.execute("DROP TABLE radicados")
    with pytest.raises(RadicadosAusenteError):
        await repo.list_visible(TENANT, GRUPO, 3)
    with pytest.raises(RadicadosAusenteError):
        await repo.get_by_event_id(eid, TENANT, GRUPO, 3)
