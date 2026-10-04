# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""AREA-09-04 — el backfill de la migración 041 deja asiento en `audit_log`.

La 041 rellena `clasificacion_fundamento` con «PENDIENTE DE MOTIVAR» en los
clasificados históricos. Cambiar el campo que alimenta el índice de la Ley 1712
art. 20 sin dejar quién/cuándo/de-qué-a-qué contradice el invariante 6; el asiento lo
pone el runner (la cadena de hashes la calcula la aplicación, no el SQL).

Mutaciones verificadas: quitar el `audit.append` → cae el de asiento; quitar el
`NOT EXISTS` → cae el de idempotencia; quitar `nivel_seguridad >= 2` → cae el de
no-clasificados.
"""

from __future__ import annotations

import json

import asyncpg
import pytest

from app.core.database import tenant_schema
from app.schemas.document import DocumentCreate
from app.services.document_service import DocumentService
from migrations import runner

pytestmark = pytest.mark.integration

_ARCHIVO_041 = "041_clasificado_con_fundamento.sql"


async def _radicado(conn, slug, nivel: int, fundamento: str | None) -> str:
    creado = await DocumentService(conn, slug).create(
        DocumentCreate(doc_type="E", subject="Prueba 041", doc_class="Petición", dest_dept_code=1),
        registered_by=None,
    )
    await conn.execute(
        "UPDATE radicados SET nivel_seguridad = $2, clasificacion_fundamento = $3 WHERE id = $1",
        creado.id,
        nivel,
        fundamento,
    )
    return creado.tracking_number


async def _asientos(conn, slug) -> list[asyncpg.Record]:
    return list(
        await conn.fetch(
            "SELECT object_ref, actor, payload FROM public.audit_log "
            "WHERE tenant_slug = $1 AND action = $2 ORDER BY ts",
            slug,
            runner.ACCION_BACKFILL_FUNDAMENTO,
        )
    )


@pytest.fixture
async def _ledger(tenant_ctx):
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    await conn.execute(f'SET search_path TO "{schema}", public')
    await runner.ledger.ensure_ledger(conn)
    await runner.ledger.record(conn, schema, runner.SERVICIO, _ARCHIVO_041)
    try:
        yield slug, conn, schema
    finally:
        await conn.execute("DELETE FROM public.schema_migrations WHERE schema_name = $1", schema)


@pytest.mark.asyncio
async def test_cada_fila_rellenada_deja_su_asiento_y_el_resto_no(_ledger) -> None:
    slug, conn, schema = _ledger
    pend = await _radicado(conn, slug, 3, "PENDIENTE DE MOTIVAR: clasificado sin fundamento")
    await _radicado(conn, slug, 3, "Ley 1712/2014 art. 18 lit. a")  # ya motivado: no se toca
    await _radicado(conn, slug, 1, "PENDIENTE DE MOTIVAR: x")  # nivel 1: fuera del índice

    assert await runner.asentar_backfill_fundamento(conn, schema, slug) == 1

    (a,) = await _asientos(conn, slug)
    assert a["object_ref"] == pend
    assert a["actor"] == runner.ACTOR_BACKFILL_FUNDAMENTO
    cuerpo = json.loads(a["payload"]) if isinstance(a["payload"], str) else a["payload"]
    assert cuerpo["de"] is None and cuerpo["a"].startswith("PENDIENTE DE MOTIVAR")
    assert cuerpo["motivo"] == "AREA-09-04"


@pytest.mark.asyncio
async def test_correrlo_dos_veces_no_duplica_el_asiento(_ledger) -> None:
    slug, conn, schema = _ledger
    await _radicado(conn, slug, 2, "PENDIENTE DE MOTIVAR: clasificado sin fundamento")
    assert await runner.asentar_backfill_fundamento(conn, schema, slug) == 1
    assert await runner.asentar_backfill_fundamento(conn, schema, slug) == 0
    assert len(await _asientos(conn, slug)) == 1


@pytest.mark.asyncio
async def test_sin_la_041_aplicada_no_asienta_nada(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    await conn.execute(f'SET search_path TO "{schema}", public')
    await runner.ledger.ensure_ledger(conn)
    await _radicado(conn, slug, 3, "PENDIENTE DE MOTIVAR: x")
    assert await runner.asentar_backfill_fundamento(conn, schema, slug) == 0
    assert not await _asientos(conn, slug)
