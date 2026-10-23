# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Migración de tenant `007_embedding_dim_384_hnsw.sql` (E21 Inc.1) contra
Postgres REAL — no se puede verificar `ALTER COLUMN ... TYPE vector(384)`, el
reemplazo del índice ANN, ni la idempotencia de un `UPDATE` masivo con un mock.

A diferencia de `tests/integration/conftest.py::tenant_ctx` (que aplica TODAS
las migraciones de tenant, incluida la 007, antes de yield-ear), este archivo
provisiona el schema aplicando 001-006 primero (dejando la columna en su estado
PRE-migración: `vector(64) NOT NULL`), inserta una fila con un embedding real de
dim 64, y SOLO ENTONCES aplica 007 — para observar la transición.

Cubre:
  - Los embeddings dim-64 existentes quedan NULL (centinela) tras 007; `texto`
    NO se toca (sobrevive para el backfill de `app/ops/reembed.py`).
  - La columna queda `vector(384)`.
  - El índice `ivfflat` viejo (`ix_knowledge_embedding`) desaparece; el nuevo
    `ix_knowledge_embedding_hnsw` (HNSW, `vector_cosine_ops`) existe.
  - Idempotencia: re-aplicar 007 una segunda vez no falla y no reintroduce
    ningún embedding (el `UPDATE ... WHERE embedding IS NOT NULL` sobre una
    tabla ya vacía de embeddings no hace nada; el `ALTER COLUMN` y los
    `DROP/CREATE INDEX IF EXISTS` son naturalmente re-ejecutables).
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest

from migrations import runner

pytestmark = pytest.mark.integration

_PRE_007_FILES = {
    "001_knowledge.sql",
    "002_nivel_seguridad_failclosed.sql",
    "003_knowledge_upsert.sql",
    "004_dead_letter.sql",
    "005_tombstone_nullable_embedding.sql",
    "006_voided_column.sql",
}


def _make_schema() -> str:
    return f"tenant_itest_mig007_{uuid.uuid4().hex[:8]}"


async def _apply(conn: asyncpg.Connection, schema: str, path) -> None:  # noqa: ANN001
    async with conn.transaction():
        await conn.execute(f'SET LOCAL search_path = "{schema}"')
        await conn.execute(path.read_text())


@pytest.fixture
def migration_007_path():
    return next(
        p for p in runner._sql_files(runner.TENANT_DIR) if p.name == "007_embedding_dim_384_hnsw.sql"
    )


@pytest.mark.integration
async def test_migration_007_nulls_old_embeddings_upgrades_column_and_index(
    db_conn: asyncpg.Connection, migration_007_path
) -> None:
    schema = _make_schema()
    await db_conn.execute(f'CREATE SCHEMA "{schema}"')
    try:
        for path in runner._sql_files(runner.TENANT_DIR):
            if path.name not in _PRE_007_FILES:
                continue
            await _apply(db_conn, schema, path)
        await db_conn.execute(f'SET search_path = "{schema}"')

        # Fila PRE-migración: embedding real de dim 64 (formato de la columna
        # original, migración 001).
        literal_64 = "[" + ",".join("0.100000" for _ in range(64)) + "]"
        await db_conn.execute(
            """
            INSERT INTO knowledge_chunk
                (source_type, source_ref, texto, embedding, nivel_seguridad)
            VALUES ('radicado', 'MIG007-1', 'contenido dim64 pre-migracion', $1::public.vector, 1)
            """,
            literal_64,
        )

        # Fila que YA era un centinela (tombstone real, texto vacío, embedding
        # NULL) — no debe verse afectada por el backfill posterior ni romper el
        # UPDATE de la migración.
        await db_conn.execute(
            """
            INSERT INTO knowledge_chunk
                (source_type, source_ref, texto, embedding, nivel_seguridad)
            VALUES ('radicado', 'MIG007-2', '', NULL, 3)
            """
        )

        await _apply(db_conn, schema, migration_007_path)

        row1 = await db_conn.fetchrow(
            "SELECT embedding IS NULL AS is_null, texto FROM knowledge_chunk WHERE source_ref = 'MIG007-1'"
        )
        assert row1["is_null"] is True, "el embedding dim-64 debe quedar NULL (centinela) tras 007"
        assert row1["texto"] == "contenido dim64 pre-migracion", (
            "texto NO se toca: el backfill (app/ops/reembed.py) lo necesita intacto"
        )

        row2 = await db_conn.fetchrow(
            "SELECT embedding IS NULL AS is_null, texto FROM knowledge_chunk WHERE source_ref = 'MIG007-2'"
        )
        assert row2["is_null"] is True
        assert row2["texto"] == ""

        col_type = await db_conn.fetchval(
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
            "WHERE attrelid = $1::regclass AND attname = 'embedding' AND NOT attisdropped",
            f'"{schema}".knowledge_chunk',
        )
        # sin `public` en el path el tipo (de pgvector, en public) sale calificado
        assert col_type in ("vector(384)", "public.vector(384)")

        hnsw_indexdef = await db_conn.fetchval(
            "SELECT indexdef FROM pg_indexes "
            "WHERE schemaname = $1 AND indexname = 'ix_knowledge_embedding_hnsw'",
            schema,
        )
        assert hnsw_indexdef is not None, "el índice HNSW debe existir tras 007"
        assert "hnsw" in hnsw_indexdef.lower()
        assert "vector_cosine_ops" in hnsw_indexdef

        old_ivfflat_exists = await db_conn.fetchval(
            "SELECT count(*) FROM pg_indexes WHERE schemaname = $1 AND indexname = 'ix_knowledge_embedding'",
            schema,
        )
        assert old_ivfflat_exists == 0, "el índice ivfflat viejo debe haberse eliminado"

        # --- Idempotencia: re-aplicar 007 no debe fallar ni reintroducir datos ---
        await _apply(db_conn, schema, migration_007_path)

        row1_again = await db_conn.fetchrow(
            "SELECT embedding IS NULL AS is_null FROM knowledge_chunk WHERE source_ref = 'MIG007-1'"
        )
        assert row1_again["is_null"] is True

        col_type_again = await db_conn.fetchval(
            "SELECT format_type(atttypid, atttypmod) FROM pg_attribute "
            "WHERE attrelid = $1::regclass AND attname = 'embedding' AND NOT attisdropped",
            f'"{schema}".knowledge_chunk',
        )
        assert col_type_again in ("vector(384)", "public.vector(384)")

        hnsw_count_after_second_apply = await db_conn.fetchval(
            "SELECT count(*) FROM pg_indexes WHERE schemaname = $1 AND indexname = 'ix_knowledge_embedding_hnsw'",
            schema,
        )
        assert hnsw_count_after_second_apply == 1, "CREATE INDEX IF NOT EXISTS no debe duplicar el índice"
    finally:
        assert schema.startswith("tenant_itest_"), (
            f"guardarraíl: me niego a hacer DROP SCHEMA CASCADE de {schema!r}"
        )
        await db_conn.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
