# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`POST /api/v1/knowledge/search` contra Postgres REAL — codec `jsonb` del pool.

Regresión de un fallo que NINGÚN mock detecta: sin el codec `jsonb` registrado en
`init` del pool (`app.core.database._init_connection`), asyncpg devuelve
`knowledge_chunk.metadata` como el TEXTO crudo `'{}'`, y `SearchHit(**dict(row))`
en el router revienta con `ValidationError` (Pydantic espera `dict`). Los tests
unit no lo ven porque mockean la conexión devolviendo dicts de Python.

Clave del diseño del test: la conexión se toma DEL POOL de la app
(`create_pool` → `pool.acquire()`), no de la conexión cruda de `tenant_ctx` — así
el test ejercita EXACTAMENTE el `init` de producción. Con el fix pasa; sin el fix
(codec ausente, o un encoder que doble-encodee) falla. También verifica que la
ESCRITURA sigue intacta: `metadata` se siembra vía `KnowledgeRepository.upsert_chunk`
(que ya hace `json.dumps` + `$N::jsonb`) y debe leerse de vuelta como el objeto
original, no como `'"{...}"'` doble-encodeado.
"""

from __future__ import annotations

import pytest

from app.core.database import _schema as tenant_schema
from app.core.database import close_pool, create_pool
from app.repositories.knowledge_repository import KnowledgeRepository
from app.routers.knowledge import SearchRequest, search
from app.services.embeddings import embed, to_pgvector

from .conftest import DSN

pytestmark = pytest.mark.integration

_META = {"area": "juridica", "anio": "2024", "reservado": False}


@pytest.mark.integration
@pytest.mark.asyncio
async def test_search_devuelve_metadata_como_dict(tenant_ctx) -> None:
    """El chunk se siembra con metadata no trivial y `/search` lo devuelve OK.

    Sin el codec del pool, `SearchHit(**dict(row))` lanzaría ValidationError
    (metadata llega como `str`) y el endpoint daría 500 — el bug que motivó el fix.
    """
    slug, seed_conn = tenant_ctx
    texto = "Certificado laboral solicitado por el ciudadano ante Talento Humano."
    await KnowledgeRepository(seed_conn).upsert_chunk(
        source_type="radicado",
        source_ref="2024-DEMO-E-000999",
        texto=texto,
        embedding_literal=to_pgvector(embed(texto)),
        metadata=_META,
        acl={},
        nivel_seguridad=1,
    )

    # Conexión DEL POOL de la app (init = _init_connection con el codec jsonb).
    pool = await create_pool(DSN)
    try:
        async with pool.acquire() as conn:
            await conn.execute(f'SET search_path = "{tenant_schema(slug)}"')

            # (a) round-trip crudo: metadata se lee como dict, no como str, y NO
            #     está doble-encodeada (la escritura json.dumps sigue intacta).
            row = await conn.fetchrow(
                "SELECT metadata FROM knowledge_chunk WHERE source_ref = $1",
                "2024-DEMO-E-000999",
            )
            assert isinstance(row["metadata"], dict), (
                "el pool debe decodificar jsonb a dict (codec ausente = str crudo)"
            )
            assert row["metadata"] == _META

            # (b) el endpoint /search construye SearchHit sin reventar y propaga la
            #     metadata como objeto.
            hits = await search(
                SearchRequest(query="certificado laboral talento humano", top_k=5),
                conn=conn,
                caller_id=None,  # clearance PUBLICA basta: el chunk es nivel 1
            )
    finally:
        await close_pool()

    assert len(hits) >= 1
    hit = next(h for h in hits if h.source_ref == "2024-DEMO-E-000999")
    assert hit.metadata == _META
    assert isinstance(hit.metadata, dict)
