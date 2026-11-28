# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El dead-letter único contra PostgreSQL REAL (ADR-021, ADR-049 §4).

`evento_dead_letter` es UNA tabla por entidad que escriben los cuatro grupos
consumidores. Aquí, una sola vez y para los cuatro:

- AREA-10-07: la PK es `(origin_group, event_id)`; dos grupos que aparcan el mismo
  evento dejan cada uno su fila y su asiento, y repetir un grupo no duplica nada.
- AREA-10-08: el clearance vive en el WHERE (listado, detalle y bloqueo), con el
  total exacto; lo oculto y lo inexistente son indistinguibles.
- AREA-02-06: una entidad no ve, no bloquea, no transiciona ni purga lo de otra.
- La retención: nunca toca lo pendiente; sin grupo, purga lo terminal de todos.
- `persistir` de punta a punta, con el `partial` de cada worker.
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager

import asyncpg
import pytest
from orpycamcp_common.database import tenant_schema
from orpycamcp_common.deadletter import RadicadosAusenteError

from docuboto.core import dead_letter
from docuboto.core.dead_letter import DeadLetterRepository
from tests.workflow.integration.conftest import insert_radicado, set_security_level

pytestmark = pytest.mark.integration

GRUPOS = ("workflow-service", "signature-service", "notification-service", "intelligence-gateway")
STREAM = "orpycamcp.document.events"


def _otro(grupo: str) -> str:
    return next(g for g in GRUPOS if g != grupo)


async def _en(tenant_ctx) -> tuple[str, asyncpg.Connection]:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    return slug, conn


async def _insertar(repo: DeadLetterRepository, slug: str, eid: str, grupo: str):
    return await repo.insert_if_new(
        event_id=eid,
        event_type="document.radicado.created",
        correlation_id=None,
        tenant_slug=slug,
        payload_json="{}",
        failure_json="{}",
        origin_stream=STREAM,
        origin_group=grupo,
    )


async def _aparcar(
    conn: asyncpg.Connection,
    slug: str,
    inner: object,
    *,
    grupo: str = GRUPOS[0],
    status: str = "pending",
    antiguedad_s: int = 0,
    doble_codificado: bool = False,
) -> str:
    eid = f"evt-{uuid.uuid4().hex[:10]}"
    texto = json.dumps({"event_id": eid, "tenant_slug": slug, "payload": inner})
    if doble_codificado:
        texto = json.dumps(texto)
    await conn.execute(
        "INSERT INTO evento_dead_letter (event_id, tenant_slug, payload, failure, origin_group, "
        "origin_stream, status, dead_lettered_at) "
        "VALUES ($1, $2, $3::jsonb, '{}'::jsonb, $4, $5, $6, now() - make_interval(secs => $7))",
        eid,
        slug,
        texto,
        grupo,
        STREAM,
        status,
        antiguedad_s,
    )
    return eid


async def _radicado(conn: asyncpg.Connection, nivel: int) -> tuple[str, uuid.UUID]:
    tn = f"2026-ITEST-E-{uuid.uuid4().hex[:8]}"
    return tn, await insert_radicado(conn, tn, nivel=nivel)


async def _ids(repo, slug, grupo, clearance, **kw) -> list[str]:
    filas, _ = await repo.list_visible(slug, grupo, clearance, limit=100, **kw)
    return [f["event_id"] for f in filas]


# ── AREA-10-07: la PK es por grupo ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_la_pk_es_por_grupo_y_evento(tenant_ctx) -> None:
    _, conn = await _en(tenant_ctx)
    pk = [
        r["attname"]
        for r in await conn.fetch(
            "SELECT a.attname FROM pg_constraint k "
            "CROSS JOIN LATERAL unnest(k.conkey) WITH ORDINALITY u(attnum, ord) "
            "JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = u.attnum "
            "WHERE k.conrelid = to_regclass('evento_dead_letter') AND k.contype = 'p' "
            "ORDER BY u.ord"
        )
    ]
    assert pk == ["origin_group", "event_id"]


@pytest.mark.parametrize("grupo", GRUPOS)
@pytest.mark.asyncio
async def test_dos_grupos_aparcan_el_mismo_evento_y_repetir_es_idempotente(tenant_ctx, grupo) -> None:
    slug, conn = await _en(tenant_ctx)
    repo = DeadLetterRepository(conn)
    eid = f"evt-{uuid.uuid4().hex[:8]}"
    assert await _insertar(repo, slug, eid, _otro(grupo)) is not None
    assert await _insertar(repo, slug, eid, grupo) is not None, (
        "el aparcamiento de este grupo se descartó en silencio por un event_id ya usado"
    )
    assert await _insertar(repo, slug, eid, grupo) is None
    grupos = {
        r["origin_group"]
        for r in await conn.fetch("SELECT origin_group FROM evento_dead_letter WHERE event_id = $1", eid)
    }
    assert grupos == {grupo, _otro(grupo)}


_WORKERS = {
    "workflow-service": "workflow.workers.document_consumer",
    "signature-service": "signature.workers.document_consumer",
    "notification-service": "notification.workers.stream_worker",
    "intelligence-gateway": "knowledge.workers.intelligence_consumer",
}


@pytest.mark.parametrize("grupo", GRUPOS)
@pytest.mark.asyncio
async def test_persistir_del_worker_deja_un_asiento_por_grupo_y_no_repite(
    tenant_ctx, grupo, monkeypatch
) -> None:
    """La ruta entera del worker (`_persist_dead_letter` = `persistir` con su grupo):
    dos grupos dejan DOS asientos; la reentrega del mismo grupo no añade uno."""
    import importlib

    slug, conn = await _en(tenant_ctx)
    worker = importlib.import_module(_WORKERS[grupo])
    otro = importlib.import_module(_WORKERS[_otro(grupo)])

    @asynccontextmanager
    async def _acquire(_slug: str):
        yield conn

    monkeypatch.setattr(dead_letter, "acquire_tenant", _acquire)
    eid = f"evt-{uuid.uuid4().hex[:8]}"
    sobre = {
        "event_id": eid,
        "event_type": "document.radicado.created",
        "tenant_slug": slug,
        "payload": {"id": "doc-1", "tracking_number": "2026-ITEST-E-000001"},
    }

    def _meta(g: str) -> dict:
        return {"dead_letter_group": g, "dead_letter_origin_stream": STREAM, "dead_letter_last_error": "x"}

    async def _asientos() -> int:
        return await conn.fetchval(
            "SELECT count(*) FROM public.audit_log WHERE object_ref = $1 AND action = 'evento.dead_letter'",
            eid,
        )

    await otro._persist_dead_letter(sobre, _meta(_otro(grupo)))
    await worker._persist_dead_letter(sobre, _meta(grupo))
    assert await _asientos() == 2, "el segundo grupo no dejó asiento: trazabilidad perdida"
    await worker._persist_dead_letter(sobre, _meta(grupo))  # reentrega del mismo grupo
    assert await _asientos() == 2
    filas = await conn.fetch(
        "SELECT origin_group, tenant_slug FROM evento_dead_letter WHERE event_id = $1", eid
    )
    assert {f["origin_group"] for f in filas} == {grupo, _otro(grupo)}
    assert {f["tenant_slug"] for f in filas} == {slug}


# ── AREA-10-08: el clearance vive en el WHERE ─────────────────────────────────


@pytest.mark.asyncio
async def test_mas_de_mil_reservadas_recientes_no_tapan_la_publica_antigua(tenant_ctx) -> None:
    slug, conn = await _en(tenant_ctx)
    repo = DeadLetterRepository(conn)
    grupo = GRUPOS[0]
    tn3, _ = await _radicado(conn, 3)
    tn1, _ = await _radicado(conn, 1)
    await conn.execute(
        "INSERT INTO evento_dead_letter (event_id, tenant_slug, payload, failure, origin_group, "
        "origin_stream, status, dead_lettered_at) "
        "SELECT 'r-' || g, $1, jsonb_build_object('payload', jsonb_build_object("
        "'tracking_number', $2::text)), '{}'::jsonb, $3, 's', 'pending', now() "
        "FROM generate_series(1, 1001) g",
        slug,
        tn3,
        grupo,
    )
    publica = await _aparcar(conn, slug, {"tracking_number": tn1}, antiguedad_s=86400 * 30)

    filas, total = await repo.list_visible(slug, grupo, 1, limit=20)
    assert [f["event_id"] for f in filas] == [publica]
    assert total == 1
    _, total3 = await repo.list_visible(slug, grupo, 3, limit=20)
    assert total3 == 1002


@pytest.mark.asyncio
async def test_la_paginacion_y_el_total_cuentan_solo_lo_visible(tenant_ctx) -> None:
    slug, conn = await _en(tenant_ctx)
    repo = DeadLetterRepository(conn)
    grupo = GRUPOS[0]
    tn1, _ = await _radicado(conn, 1)
    tn3, _ = await _radicado(conn, 3)
    for i in range(25):
        await _aparcar(conn, slug, {"tracking_number": tn1}, antiguedad_s=i)
    for i in range(30):
        await _aparcar(conn, slug, {"tracking_number": tn3}, antiguedad_s=i)

    p1, total = await repo.list_visible(slug, grupo, 1, offset=0, limit=20)
    p2, total2 = await repo.list_visible(slug, grupo, 1, offset=20, limit=20)
    pasada, total3 = await repo.list_visible(slug, grupo, 1, offset=100, limit=20)
    assert (len(p1), total) == (20, 25)
    assert (len(p2), total2) == (5, 25)
    assert (pasada, total3) == ([], 25), "un offset pasado de fin no debe dar total 0"
    assert not ({f["event_id"] for f in p1} & {f["event_id"] for f in p2})


@pytest.mark.asyncio
async def test_reclasificado_hacia_arriba_tras_aparcar_se_oculta_en_todas_las_rutas(tenant_ctx) -> None:
    slug, conn = await _en(tenant_ctx)
    repo = DeadLetterRepository(conn)
    grupo = GRUPOS[0]
    tn, rid = await _radicado(conn, 1)
    eid = await _aparcar(conn, slug, {"tracking_number": tn})
    assert await _ids(repo, slug, grupo, 1) == [eid]

    await set_security_level(conn, rid, 3)

    assert await _ids(repo, slug, grupo, 1) == []
    ajeno = f"evt-{uuid.uuid4().hex[:10]}"
    assert await repo.get_by_event_id(eid, slug, grupo, 1) is None
    assert await repo.get_by_event_id(ajeno, slug, grupo, 1) is None, (
        "oculto e inexistente deben ser indistinguibles"
    )
    async with conn.transaction():
        assert await repo.get_for_update(eid, slug, grupo, 1) is None
        assert await repo.get_for_update(ajeno, slug, grupo, 1) is None
        assert await repo.get_for_update(eid, slug, grupo, 3) is not None
    assert await repo.get_by_event_id(eid, slug, grupo, 3) is not None


@pytest.mark.asyncio
async def test_radicado_purgado_o_sin_identificadores_solo_lo_ve_clearance_3(tenant_ctx) -> None:
    slug, conn = await _en(tenant_ctx)
    repo = DeadLetterRepository(conn)
    grupo = GRUPOS[0]
    purgado = await _aparcar(conn, slug, {"tracking_number": "2026-NO-EXISTE-E-000001"})
    sin_ids = await _aparcar(conn, slug, {"asunto": "sin referencia"})
    for nivel in (1, 2):
        assert await _ids(repo, slug, grupo, nivel) == []
    assert set(await _ids(repo, slug, grupo, 3)) == {purgado, sin_ids}


@pytest.mark.asyncio
async def test_el_nivel_embebido_solo_puede_subir_y_la_duda_oculta(tenant_ctx) -> None:
    slug, conn = await _en(tenant_ctx)
    repo = DeadLetterRepository(conn)
    grupo = GRUPOS[0]
    tn, _ = await _radicado(conn, 1)
    control = await _aparcar(conn, slug, {"tracking_number": tn, "nivel_nuevo": 1})
    for raro in ("3", 3, "x", 2.0, True):
        await _aparcar(conn, slug, {"tracking_number": tn, "nivel_nuevo": raro})
    cae_a_nivel_seguridad = await _aparcar(
        conn, slug, {"tracking_number": tn, "nivel_nuevo": None, "nivel_seguridad": 3}
    )
    sin_embebido = await _aparcar(conn, slug, {"tracking_number": tn})
    visibles = set(await _ids(repo, slug, grupo, 1))
    assert visibles == {control, sin_embebido}
    assert cae_a_nivel_seguridad not in visibles
    assert len(await _ids(repo, slug, grupo, 3)) == 8


@pytest.mark.asyncio
async def test_sobre_malformado_no_rompe_el_listado_y_se_oculta(tenant_ctx) -> None:
    slug, conn = await _en(tenant_ctx)
    repo = DeadLetterRepository(conn)
    grupo = GRUPOS[0]
    abc = await _aparcar(conn, slug, {"id": "abc"})
    num = await _aparcar(conn, slug, {"id": 12345})
    doble = await _aparcar(conn, slug, {"tracking_number": "x"}, doble_codificado=True)
    lista = await _aparcar(conn, slug, [1, 2, 3])
    assert await _ids(repo, slug, grupo, 2) == []
    assert set(await _ids(repo, slug, grupo, 3)) == {abc, num, doble, lista}
    _, rid = await _radicado(conn, 1)
    por_id = await _aparcar(conn, slug, {"id": str(rid).upper()})  # UUID válido: resuelve al vivo
    assert await _ids(repo, slug, grupo, 1) == [por_id]


@pytest.mark.parametrize("grupo", GRUPOS)
@pytest.mark.asyncio
async def test_otro_grupo_y_otros_estados_no_se_ven(tenant_ctx, grupo) -> None:
    slug, conn = await _en(tenant_ctx)
    repo = DeadLetterRepository(conn)
    tn, _ = await _radicado(conn, 1)
    propio = await _aparcar(conn, slug, {"tracking_number": tn}, grupo=grupo)
    ajeno = await _aparcar(conn, slug, {"tracking_number": tn}, grupo=_otro(grupo))
    resuelto = await _aparcar(conn, slug, {"tracking_number": tn}, grupo=grupo, status="resolved")
    assert await _ids(repo, slug, grupo, 3) == [propio]
    assert await repo.get_by_event_id(ajeno, slug, grupo, 3) is None
    assert await repo.transition_status(ajeno, slug, "discarded", grupo) == 0
    assert await _ids(repo, slug, grupo, 3, status="resolved") == [resuelto]


@pytest.mark.asyncio
async def test_un_radicados_ajeno_en_el_search_path_no_se_lee(tenant_ctx) -> None:
    """El gate califica `radicados` con el schema de la entidad: si éste no la tiene,
    una homónima de otro schema del `search_path` NO la sustituye."""
    slug, conn = await _en(tenant_ctx)
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
        await _aparcar(conn, slug, {"tracking_number": "X-1"})
        await conn.execute("DROP TABLE radicados CASCADE")
        await conn.execute(f'SET search_path TO "{tenant_schema(slug)}", "{ajeno}"')
        with pytest.raises(RadicadosAusenteError):
            await repo.list_visible(slug, GRUPOS[0], 1)
    finally:
        await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
        await conn.execute(f'DROP SCHEMA IF EXISTS "{ajeno}" CASCADE')


@pytest.mark.asyncio
async def test_sin_tabla_radicados_es_error_explicito_no_lista_vacia(tenant_ctx) -> None:
    slug, conn = await _en(tenant_ctx)
    repo = DeadLetterRepository(conn)
    eid = await _aparcar(conn, slug, {"tracking_number": "x"})
    await conn.execute("DROP TABLE radicados CASCADE")
    with pytest.raises(RadicadosAusenteError):
        await repo.list_visible(slug, GRUPOS[0], 3)
    with pytest.raises(RadicadosAusenteError):
        await repo.get_by_event_id(eid, slug, GRUPOS[0], 3)


# ── AREA-02-06: una entidad no toca la DLQ de otra ────────────────────────────


@pytest.mark.parametrize("grupo", GRUPOS)
@pytest.mark.asyncio
async def test_una_entidad_no_ve_ni_muta_ni_purga_la_dlq_de_otra(two_tenant_ctx, grupo) -> None:
    a, b, conn = two_tenant_ctx
    # La DLQ se resuelve en el schema de `b`; el gate califica `radicados` con el de quien pregunta.
    await conn.execute(f'SET search_path TO "{tenant_schema(b)}", "{tenant_schema(a)}"')
    repo = DeadLetterRepository(conn)
    eid = f"evt-{uuid.uuid4().hex[:8]}"
    assert await _insertar(repo, b, eid, grupo) is not None

    assert await repo.get_by_event_id(eid, b, grupo, 3) is not None  # control positivo
    assert await repo.get_by_event_id(eid, a, grupo, 3) is None
    async with conn.transaction():
        assert await repo.get_for_update(eid, a, grupo, 3) is None
    assert await repo.transition_status(eid, a, "discarded", grupo) == 0
    filas_a, total_a = await repo.list_visible(a, grupo, 3)
    assert eid not in [f["event_id"] for f in filas_a] and total_a == 0

    assert await repo.transition_status(eid, b, "resolved", grupo) == 1
    await conn.execute(
        "UPDATE evento_dead_letter SET resolved_at = now() - interval '400 days' WHERE event_id = $1",
        eid,
    )
    assert await dead_letter.purgar_terminales(conn, a, dias=30) == 0
    assert await conn.fetchval("SELECT count(*) FROM evento_dead_letter WHERE event_id = $1", eid) == 1
    assert await dead_letter.purgar_terminales(conn, b, dias=30) == 1


# ── Retención ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_la_purga_sin_grupo_retira_lo_terminal_viejo_de_todos_y_nunca_lo_pendiente(
    tenant_ctx,
) -> None:
    slug, conn = await _en(tenant_ctx)
    viejos = {}
    for grupo in GRUPOS:
        viejos[grupo] = await _aparcar(conn, slug, {}, grupo=grupo, status="resolved")
    descartado = await _aparcar(conn, slug, {}, grupo=GRUPOS[1], status="discarded")
    reciente = await _aparcar(conn, slug, {}, grupo=GRUPOS[2], status="resolved")
    pendiente_viejo = await _aparcar(conn, slug, {}, grupo=GRUPOS[3], antiguedad_s=86400 * 400)
    await conn.execute(
        "UPDATE evento_dead_letter SET resolved_at = now() - interval '400 days' "
        "WHERE event_id = ANY($1::text[])",
        [*viejos.values(), descartado],
    )
    await conn.execute(
        "UPDATE evento_dead_letter SET resolved_at = now() WHERE event_id = $1", reciente
    )

    assert await dead_letter.purgar_terminales(conn, slug, dias=90) == len(GRUPOS) + 1
    quedan = {r["event_id"] for r in await conn.fetch("SELECT event_id FROM evento_dead_letter")}
    assert quedan == {reciente, pendiente_viejo}


@pytest.mark.asyncio
async def test_la_purga_rechaza_una_ventana_absurda(tenant_ctx) -> None:
    slug, conn = await _en(tenant_ctx)
    with pytest.raises(ValueError):
        await dead_letter.purgar_terminales(conn, slug, dias=0)
