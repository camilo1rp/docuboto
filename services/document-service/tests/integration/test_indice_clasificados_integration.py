# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Integración (Postgres REAL) del índice de clasificados (E08, Ley 1712 art. 20).

Nivel repositorio: verifica las garantías de MOTOR — persistencia/limpieza de la
metadata de clasificación vigente y la completitud + content-safety de la consulta
del índice. El flujo del servicio (que audita) se cubre en unit.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from app.core.database import tenant_schema
from app.repositories.document_repository import DocumentRepository

pytestmark = pytest.mark.integration


async def _seed_radicado(conn: asyncpg.Connection, *, doc_type: str = "E", nivel: int = 1) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO radicados (tracking_number, doc_type, year, sequence, subject, "
        "tenant_code, nivel_seguridad) VALUES ($1, $2, 2026, 1, 'ASUNTO SECRETO', 'DEMO', $3) "
        "RETURNING id",
        f"2026-DEMO-{doc_type}-{uuid.uuid4().hex[:6]}",
        doc_type,
        nivel,
    )


@pytest.mark.asyncio
async def test_set_classification_persists_meta_and_indice_lists(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path = "{tenant_schema(slug)}", public')
    repo = DocumentRepository(conn, slug)
    rid = await _seed_radicado(conn, nivel=1)

    await repo.set_classification(
        rid, 3, fundamento="Art. 18 lit. b Ley 1712", causal="Datos personales", plazo_meses=None
    )
    row = await conn.fetchrow(
        "SELECT nivel_seguridad, clasificacion_fundamento, clasificacion_causal, "
        "clasificado_at FROM radicados WHERE id=$1",
        rid,
    )
    assert row["nivel_seguridad"] == 3
    assert row["clasificacion_fundamento"] == "Art. 18 lit. b Ley 1712"
    assert row["clasificacion_causal"] == "Datos personales"
    assert row["clasificado_at"] is not None

    indice = await repo.indice_clasificados()
    assert len(indice) == 1
    assert indice[0]["tracking_number"] is not None
    assert indice[0]["clasificacion_fundamento"] == "Art. 18 lit. b Ley 1712"
    # Content-safety estructural: la consulta del índice NO trae `subject`.
    assert "subject" not in indice[0].keys()


@pytest.mark.asyncio
async def test_declassify_clears_meta_and_removes_from_indice(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path = "{tenant_schema(slug)}", public')
    repo = DocumentRepository(conn, slug)
    rid = await _seed_radicado(conn, nivel=1)

    await repo.set_classification(rid, 2, fundamento="F", causal="C", plazo_meses=12)
    assert len(await repo.indice_clasificados()) == 1

    # Desclasificar (nivel 1) limpia la metadata y saca del índice.
    await repo.set_classification(rid, 1)
    row = await conn.fetchrow(
        "SELECT nivel_seguridad, clasificacion_fundamento, clasificacion_causal, "
        "clasificacion_plazo_meses, clasificado_at FROM radicados WHERE id=$1",
        rid,
    )
    assert row["nivel_seguridad"] == 1
    assert row["clasificacion_fundamento"] is None
    assert row["clasificacion_causal"] is None
    assert row["clasificacion_plazo_meses"] is None
    assert row["clasificado_at"] is None
    assert await repo.indice_clasificados() == []


@pytest.mark.asyncio
async def test_indice_complete_and_orthogonal_to_clearance(tenant_ctx) -> None:
    """El índice incluye TODOS los nivel≥2 (2 y 3), excluye PUBLICA, sin filtro de clearance."""
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path = "{tenant_schema(slug)}", public')
    repo = DocumentRepository(conn, slug)

    pub = await _seed_radicado(conn, doc_type="E", nivel=1)
    res = await _seed_radicado(conn, doc_type="S", nivel=1)
    cla = await _seed_radicado(conn, doc_type="I", nivel=1)
    await repo.set_classification(res, 2, fundamento="reservada")
    await repo.set_classification(cla, 3, fundamento="clasificada")

    indice = await repo.indice_clasificados()
    niveles = sorted(r["nivel_seguridad"] for r in indice)
    assert niveles == [2, 3]  # el PUBLICA (nivel 1) queda fuera
    trackings = {r["tracking_number"] for r in indice}
    pub_tracking = await conn.fetchval("SELECT tracking_number FROM radicados WHERE id=$1", pub)
    assert pub_tracking not in trackings


@pytest.mark.asyncio
async def test_migration_018_idempotent(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    from pathlib import Path

    sql = (
        Path(__file__).parents[2] / "migrations" / "tenant" / "018_radicado_clasificacion_meta.sql"
    ).read_text()
    async with conn.transaction():
        await conn.execute(f'SET LOCAL search_path = "{tenant_schema(slug)}", public')
        await conn.execute(sql)  # segunda pasada, no-op sin error
