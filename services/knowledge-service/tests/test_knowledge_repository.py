# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests unitarios del repositorio de conocimiento (E21): upsert idempotente
(ON CONFLICT + GREATEST), fijación exacta de nivel, y purga por source.

Nota de alcance: con una conexión mockeada no se puede ejercitar la garantía real
de Postgres (unicidad + GREATEST evaluados en el servidor); estos tests verifican
la FORMA del SQL emitido — que es el nivel de cobertura correcto para este MVP sin
tier de integración (ver [[tier-integracion-atomicidad]] en memoria de proyecto).
La semántica de idempotencia y monotonicidad de nivel es, en última instancia, una
garantía del motor de BD sobre la restricción `ux_knowledge_source` (migración 003)
y la expresión `GREATEST(...)` del UPDATE, no del código Python.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.repositories.knowledge_repository import KnowledgeRepository

NOW = datetime.now(tz=UTC)


@pytest.mark.asyncio
async def test_upsert_chunk_is_idempotent_on_conflict_and_uses_greatest() -> None:
    """ON CONFLICT (source_type, source_ref) DO UPDATE con GREATEST de nivel.

    El GREATEST es el candado de seguridad: un `created` reprocesado tarde nunca
    baja el nivel de un chunk que un `reclassified` posterior ya subió.
    """
    conn = AsyncMock()
    cid = uuid.uuid4()
    conn.fetchrow.return_value = {
        "id": cid,
        "source_type": "radicado",
        "source_ref": "2026-DEMO-E-000001",
        "created_at": NOW,
    }
    repo = KnowledgeRepository(conn)

    row = await repo.upsert_chunk(
        source_type="radicado",
        source_ref="2026-DEMO-E-000001",
        texto="hola",
        embedding_literal="[0.1,0.2]",
        metadata={"doc_type": "E"},
        acl={},
        nivel_seguridad=2,
    )

    assert row["source_ref"] == "2026-DEMO-E-000001"
    conn.fetchrow.assert_called_once()
    sql = conn.fetchrow.call_args.args[0]
    assert "ON CONFLICT (source_type, source_ref) DO UPDATE" in sql
    assert "GREATEST(" in sql
    assert "knowledge_chunk.nivel_seguridad" in sql
    assert "EXCLUDED.nivel_seguridad" in sql
    assert "::vector" in sql


@pytest.mark.asyncio
async def test_update_nivel_by_source_sets_exact_level_without_greatest() -> None:
    """update_nivel_by_source fija el nivel EXACTO en conflicto — sin GREATEST:
    una baja de nivel por reclasificación es legítima, no una regresión que deba
    evitarse. Es un UPSERT (residual ALTA cerrado, config stub por defecto): si la
    fila no existe (reclassified llegó antes que created), deja un centinela en
    vez de perder el nivel en un UPDATE de 0 filas."""
    conn = AsyncMock()
    cid = uuid.uuid4()
    conn.fetchrow.return_value = {
        "id": cid,
        "source_type": "radicado",
        "source_ref": "2026-DEMO-E-000001",
        "created_at": NOW,
    }
    repo = KnowledgeRepository(conn)

    row = await repo.update_nivel_by_source("radicado", "2026-DEMO-E-000001", 1)

    assert row["source_ref"] == "2026-DEMO-E-000001"
    conn.fetchrow.assert_called_once()
    sql = conn.fetchrow.call_args.args[0]
    assert "ON CONFLICT (source_type, source_ref) DO UPDATE" in sql
    assert "nivel_seguridad = EXCLUDED.nivel_seguridad" in sql
    assert "GREATEST" not in sql
    assert conn.fetchrow.call_args.args[1:] == ("radicado", "2026-DEMO-E-000001", 1)


@pytest.mark.asyncio
async def test_delete_by_source_deletes_by_source_type_and_ref() -> None:
    conn = AsyncMock()
    conn.execute.return_value = "DELETE 1"
    repo = KnowledgeRepository(conn)

    await repo.delete_by_source("radicado", "2026-DEMO-E-000001")

    conn.execute.assert_called_once()
    args = conn.execute.call_args.args
    assert "DELETE FROM knowledge_chunk" in args[0]
    assert args[1:] == ("radicado", "2026-DEMO-E-000001")


@pytest.mark.asyncio
async def test_get_nivel_by_source_reads_current_level() -> None:
    conn = AsyncMock()
    conn.fetchval.return_value = 2
    repo = KnowledgeRepository(conn)

    result = await repo.get_nivel_by_source("radicado", "2026-DEMO-E-000001")

    assert result == 2
    conn.fetchval.assert_called_once()
    args = conn.fetchval.call_args.args
    assert "SELECT nivel_seguridad FROM knowledge_chunk" in args[0]
    assert args[1:] == ("radicado", "2026-DEMO-E-000001")


@pytest.mark.asyncio
async def test_get_nivel_by_source_returns_none_when_absent() -> None:
    conn = AsyncMock()
    conn.fetchval.return_value = None
    repo = KnowledgeRepository(conn)

    result = await repo.get_nivel_by_source("radicado", "does-not-exist")

    assert result is None


@pytest.mark.asyncio
async def test_tombstone_by_source_upserts_with_greatest_and_clears_material() -> None:
    """Fix #1 (tenant-security-auditor): tombstone_by_source es un UPSERT (no un
    UPDATE) que vacía texto/embedding/metadata pero conserva la fila con el nivel
    más alto vía GREATEST — el mismo candado de `upsert_chunk`, aplicado también
    cuando la fila no existía todavía (reordenamiento reclassified-antes-de-created)."""
    conn = AsyncMock()
    cid = uuid.uuid4()
    conn.fetchrow.return_value = {
        "id": cid,
        "source_type": "radicado",
        "source_ref": "2026-DEMO-E-000001",
        "created_at": NOW,
    }
    repo = KnowledgeRepository(conn)

    row = await repo.tombstone_by_source("radicado", "2026-DEMO-E-000001", 3)

    assert row["source_ref"] == "2026-DEMO-E-000001"
    conn.fetchrow.assert_called_once()
    sql = conn.fetchrow.call_args.args[0]
    args = conn.fetchrow.call_args.args
    assert "ON CONFLICT (source_type, source_ref) DO UPDATE" in sql
    assert "GREATEST(" in sql
    assert "knowledge_chunk.nivel_seguridad" in sql
    assert "embedding" in sql and "NULL" in sql
    assert "texto" in sql
    assert args[1:] == ("radicado", "2026-DEMO-E-000001", 3)


@pytest.mark.asyncio
async def test_search_excludes_tombstones_via_embedding_not_null() -> None:
    """Fix #1: `search` nunca devuelve un tombstone (embedding NULL), sea cual sea
    su nivel_seguridad — cierra el search-leak si alguna vez quedara uno vivo."""
    conn = AsyncMock()
    conn.fetch.return_value = []
    repo = KnowledgeRepository(conn)

    await repo.search(
        embedding_literal="[0.1,0.2]", top_k=5, max_clearance=3, acl_filter=None
    )

    conn.fetch.assert_called_once()
    sql = conn.fetch.call_args.args[0]
    assert "embedding IS NOT NULL" in sql
