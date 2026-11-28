# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""ADR-049 — el bus de eventos en PostgreSQL, contra la tabla de verdad.

Lo que solo se ve con Postgres real:
- publicar va en la transacción del llamante: si revierte, el evento no existe;
- cada grupo toma cada evento una vez, y dos grupos lo toman por separado;
- un evento confirmado TARDE con un `seq` menor se entrega igual (no hay cursor);
- la reclamación reintenta, aparca en la quinta entrega y solo confirma si el
  dead-letter quedó escrito (R-4);
- la purga no borra lo que un grupo tiene sin confirmar.
"""

from __future__ import annotations

import asyncpg
import pytest
from orpycamcp_common import events
from orpycamcp_common.database import tenant_schema

pytestmark = pytest.mark.integration

STREAM = "orpycamcp.document.events"


async def _en(tenant_ctx) -> tuple[str, asyncpg.Connection]:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path TO "{tenant_schema(slug)}"')
    return slug, conn


async def _emitir(conn, slug: str, clave: str) -> dict:
    return await events.emit(
        conn,
        STREAM,
        "document.radicado.created",
        tenant_slug=slug,
        dedup_key=clave,
        payload={"id": clave},
    )


@pytest.mark.asyncio
async def test_publicar_va_en_la_transaccion_del_llamante(tenant_ctx) -> None:
    slug, conn = await _en(tenant_ctx)

    class _Revierte(Exception):
        pass

    with pytest.raises(_Revierte):
        async with conn.transaction():
            await _emitir(conn, slug, "R-1")
            raise _Revierte

    async with conn.transaction():
        evento = await _emitir(conn, slug, "R-2")

    assert await conn.fetchval("SELECT count(*) FROM eventos") == 1
    lote = await events.leer(conn, "g", [STREAM])
    assert [events.parse_fields(c)["event_id"] for _, c in lote] == [evento["event_id"]]


@pytest.mark.asyncio
async def test_el_instante_es_el_de_la_base(tenant_ctx) -> None:
    slug, conn = await _en(tenant_ctx)
    evento = await _emitir(conn, slug, "T-1")
    publicado = await conn.fetchval("SELECT publicado_en FROM eventos")
    from datetime import datetime

    assert abs((datetime.fromisoformat(evento["occurred_at"]) - publicado).total_seconds()) < 1


@pytest.mark.asyncio
async def test_cada_grupo_toma_cada_evento_una_vez(tenant_ctx) -> None:
    slug, conn = await _en(tenant_ctx)
    for i in range(3):
        await _emitir(conn, slug, f"G-{i}")

    a = await events.leer(conn, "workflow-service", [STREAM])
    assert len(a) == 3
    assert await events.leer(conn, "workflow-service", [STREAM]) == []
    # Otro grupo ve los mismos eventos, por separado.
    assert len(await events.leer(conn, "notification-service", [STREAM])) == 3
    # Un stream que el grupo no pide no se le entrega.
    assert await events.leer(conn, "otro", ["orpycamcp.workflow.events"]) == []


@pytest.mark.asyncio
async def test_un_commit_tardio_con_seq_menor_se_entrega(tenant_ctx) -> None:
    slug, conn = await _en(tenant_ctx)
    # El mismo DSN que la conexión del fixture (el conftest normaliza el esquema).
    from tests.document.integration.conftest import DSN

    otra = await asyncpg.connect(DSN)
    try:
        await otra.execute(f'SET search_path TO "{tenant_schema(slug)}"')
        tx = otra.transaction()
        await tx.start()
        tardio = await _emitir(otra, slug, "TARDE")  # toma el seq menor, sin confirmar
        temprano = await _emitir(conn, slug, "PRONTO")  # seq mayor, confirmado ya
        primero = await events.leer(conn, "g", [STREAM])
        assert [events.parse_fields(c)["event_id"] for _, c in primero] == [temprano["event_id"]]
        await tx.commit()
        segundo = await events.leer(conn, "g", [STREAM])
        assert [events.parse_fields(c)["event_id"] for _, c in segundo] == [tardio["event_id"]]
    finally:
        await otra.close()


@pytest.mark.asyncio
async def test_lo_confirmado_no_se_reclama_y_lo_pendiente_si(tenant_ctx) -> None:
    slug, conn = await _en(tenant_ctx)
    await _emitir(conn, slug, "OK")
    await _emitir(conn, slug, "MAL")
    lote = await events.leer(conn, "g", [STREAM])
    ok_id = lote[0][0]
    await events.ack(conn, "g", ok_id)
    await conn.execute("UPDATE evento_entregas SET reclamado_en = now() - interval '5 minutes'")

    vistos: list[str] = []

    async def handler(campos):
        vistos.append(events.parse_fields(campos)["dedup_key"])

    stats = await events.reclaim_stale(conn, "g", handler=handler)
    assert vistos == ["MAL"]
    assert stats.reprocessed_ok == 1
    assert await conn.fetchval(
        "SELECT count(*) FROM evento_entregas WHERE confirmado_en IS NULL"
    ) == 0


async def _envejecer(conn) -> None:
    await conn.execute(
        "UPDATE evento_entregas SET reclamado_en = now() - interval '5 minutes' "
        "WHERE confirmado_en IS NULL"
    )


@pytest.mark.asyncio
async def test_la_quinta_entrega_fallida_va_al_dead_letter_con_su_error(tenant_ctx) -> None:
    slug, conn = await _en(tenant_ctx)
    await _emitir(conn, slug, "VENENO")
    await events.leer(conn, "g", [STREAM])  # 1.ª entrega (el bucle normal)

    async def handler(_campos):
        raise RuntimeError("se rompe siempre")

    aparcados: list[tuple[dict, dict]] = []

    async def on_dead_letter(envelope, meta):
        aparcados.append((envelope, meta))

    for _ in range(4):  # entregas 2..5
        await _envejecer(conn)
        await events.reclaim_stale(conn, "g", handler=handler, on_dead_letter=on_dead_letter)

    assert len(aparcados) == 1
    envelope, meta = aparcados[0]
    assert envelope["dedup_key"] == "VENENO"
    assert meta["dead_letter_delivery_count"] == "5"
    assert meta["dead_letter_origin_stream"] == STREAM
    assert "se rompe siempre" in meta["dead_letter_last_error"]
    assert await conn.fetchval(
        "SELECT count(*) FROM evento_entregas WHERE confirmado_en IS NULL"
    ) == 0


@pytest.mark.asyncio
async def test_sin_rastro_durable_no_se_confirma(tenant_ctx) -> None:
    """R-4: si escribir el dead-letter falla, la entrega sigue pendiente."""
    slug, conn = await _en(tenant_ctx)
    await _emitir(conn, slug, "VENENO")
    await events.leer(conn, "g", [STREAM])
    await conn.execute("UPDATE evento_entregas SET entregas = 5")

    async def handler(_campos):
        raise AssertionError("una huérfana en el tope no se reintenta")

    async def falla(_envelope, _meta):
        raise ConnectionError("la base del dead-letter no responde")

    await _envejecer(conn)
    stats = await events.reclaim_stale(conn, "g", handler=handler, on_dead_letter=falla)
    assert stats.dead_lettered == 0
    assert await conn.fetchval(
        "SELECT count(*) FROM evento_entregas WHERE confirmado_en IS NULL"
    ) == 1

    aparcados: list[dict] = []

    async def ok(_envelope, meta):
        aparcados.append(meta)

    await _envejecer(conn)
    stats = await events.reclaim_stale(conn, "g", handler=handler, on_dead_letter=ok)
    assert stats.dead_lettered == 1
    # La huérfana se aparca con el marcador explícito, nunca con un texto vacío.
    assert aparcados[0]["dead_letter_last_error"] == events.LASTERR_DESCONOCIDO


@pytest.mark.asyncio
async def test_la_purga_respeta_lo_pendiente(tenant_ctx) -> None:
    slug, conn = await _en(tenant_ctx)
    await _emitir(conn, slug, "LEIDO")
    await _emitir(conn, slug, "PENDIENTE")
    lote = await events.leer(conn, "g", [STREAM])
    await events.ack(conn, "g", lote[0][0])
    await conn.execute("UPDATE eventos SET publicado_en = now() - interval '40 days'")

    assert await events.purgar(conn, dias=30) == 1
    quedan = [r["event_id"] for r in await conn.fetch("SELECT event_id FROM eventos")]
    assert quedan == [lote[1][0]]
    # La entrega del purgado se fue con él: no queda una dedup huérfana.
    assert await conn.fetchval(
        "SELECT count(*) FROM evento_entregas WHERE event_id = $1", lote[0][0]
    ) == 0
