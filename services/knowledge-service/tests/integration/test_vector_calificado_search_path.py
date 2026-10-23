# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`OPERATOR(public.<=>)` y `$n::public.vector` contra Postgres REAL, con el path = solo el tenant.

Sin `public` en el `search_path` (ADR-002, addendum C-7), la búsqueda semántica nombra la
extensión calificada. Lo que un mock no dice: que el SQL calificado EJECUTA y que el
planificador sigue casando el operador con la opclass del índice HNSW (la casa por OID).

Mutaciones: `$1::public.vector` -> `$1::vector` cae en `test_search_real_con_el_path_solo_tenant`
(`type "vector" does not exist`); `OPERATOR(public.<=>)` -> `<=>` cae igual (`operator does
not exist`); quitar el índice HNSW cae en el EXPLAIN.
"""

from __future__ import annotations

import pytest

from app.core.database import _schema as tenant_schema
from app.repositories.knowledge_repository import KnowledgeRepository
from app.services.embeddings import embed, to_pgvector

pytestmark = pytest.mark.integration


async def _sembrar(conn, texto: str, ref: str) -> None:
    await KnowledgeRepository(conn).upsert_chunk(
        source_type="radicado",
        source_ref=ref,
        texto=texto,
        embedding_literal=to_pgvector(embed(texto)),
        metadata={},
        acl={},
        nivel_seguridad=1,
    )


@pytest.mark.asyncio
async def test_search_real_con_el_path_solo_tenant(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path = "{tenant_schema(slug)}"')
    assert await conn.fetchval("SHOW search_path") in (
        tenant_schema(slug), f'"{tenant_schema(slug)}"'
    )
    texto = "Certificado laboral solicitado por el ciudadano ante Talento Humano."
    await _sembrar(conn, texto, "2026-DEMO-E-000777")

    # El repositorio REAL (el SQL de producción), no una copia: es lo que nombra `public.vector`.
    filas = await KnowledgeRepository(conn).search(
        embedding_literal=to_pgvector(embed(texto)), top_k=3, max_clearance=3, acl_filter=None
    )
    assert filas and filas[0]["source_ref"] == "2026-DEMO-E-000777"
    assert filas[0]["score"] == pytest.approx(1.0, abs=1e-4)


@pytest.mark.asyncio
async def test_el_operador_calificado_sigue_usando_el_indice_hnsw(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path = "{tenant_schema(slug)}"')
    await _sembrar(conn, "Solicitud de licencia de maternidad", "2026-DEMO-E-000778")
    async with conn.transaction():
        await conn.execute("SET LOCAL enable_seqscan = off")
        plan = "\n".join(
            r[0]
            for r in await conn.fetch(
                "EXPLAIN SELECT source_ref FROM knowledge_chunk k "
                "ORDER BY k.embedding OPERATOR(public.<=>) $1::public.vector LIMIT 3",
                to_pgvector(embed("licencia")),
            )
        )
    assert "ix_knowledge_embedding_hnsw" in plan, plan


@pytest.mark.asyncio
async def test_sin_calificar_el_tipo_no_existe_con_el_path_solo_tenant(tenant_ctx) -> None:
    """Control positivo del invariante: con el path nuevo, `::vector` a secas ya no resuelve."""
    import asyncpg

    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path = "{tenant_schema(slug)}"')
    with pytest.raises(asyncpg.PostgresError):
        await conn.fetchval("SELECT '[1,2]'::vector")
