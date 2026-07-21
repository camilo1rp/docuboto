# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`POST /api/v1/knowledge/rag` (E21 Inc.3, Patrón B) contra Postgres REAL.

Invoca la función del router DIRECTAMENTE (mismo patrón que
`tests/integration/test_antecedentes_acl_pivot.py`), con `conn` = la conexión
real del tenant desechable (`tenant_ctx`). El `LLMProvider` se sustituye SIEMPRE
por `_RecordingProvider` (doble en memoria) — este archivo nunca llama a un LLM
real ni instancia un SDK; lo que sí es real es Postgres (el `WHERE
nivel_seguridad <= max_clearance` de `KnowledgeRepository.search`, la columna
`nivel_seguridad` que lee la barrera dura, la tabla `kb_suggestions`, y
`public.audit_log`).

Cubre lo que NINGÚN mock puede demostrar:
  (a) barrera dura `is_generatable` operando sobre `nivel_seguridad` REAL leído
      de `knowledge_chunk` (columna, no un dict fabricado a mano) — un chunk
      RESERVADA/CLASIFICADA nunca llega al `LLMProvider` cuando el proveedor es
      externo, incluso con clearance alto (para que la ÚNICA barrera que pueda
      actuar sea la de residencia, no el pre-filtro ACL).
  (b) el pre-filtro ACL de `KnowledgeRepository.search` (capa 1, motor real)
      excluye un chunk CLASIFICADA del resultado ANTES de que la barrera dura
      (capa 2) tenga oportunidad de actuar — con clearance PUBLICA, ni siquiera
      un proveedor LOCAL (que no aplicaría la barrera) llega a verlo.
  (c) `kb_suggestions`: fila persistida con `proveedor`/`modelo` en columnas
      separadas, aislada por tenant (mismo mecanismo de schema que
      `knowledge_chunk`); `audit_log` con proveedor+modelo+confianza, SIN el
      contenido de la respuesta.
  (d) migración `008_kb_suggestions.sql` idempotente contra Postgres real.
  (e) un fallo del provider no deja ningún efecto secundario parcial (ni fila
      en `kb_suggestions` ni asiento en `audit_log`).
"""

from __future__ import annotations

import json
import uuid

import asyncpg
import pytest
from fastapi import HTTPException
from starlette.requests import Request

import app.routers.knowledge as knowledge_router
from app.core import config as cfg_module
from app.core.clearance import CLASIFICADA, PUBLICA, RESERVADA
from app.core.database import _schema as tenant_schema
from app.repositories.knowledge_repository import KnowledgeRepository
from app.routers.knowledge import RagRequest, rag
from app.services.embeddings import embed, to_pgvector
from app.services.llm import GeneratedAnswer, RAGUnavailableError, RetrievedChunk
from migrations import runner

from .conftest import DSN, _make_slug

pytestmark = pytest.mark.integration


async def _seed(
    conn: asyncpg.Connection, *, source_ref: str, texto: str, nivel: int, source_type: str = "radicado"
) -> None:
    literal = to_pgvector(embed(texto))
    await KnowledgeRepository(conn).upsert_chunk(
        source_type=source_type,
        source_ref=source_ref,
        texto=texto,
        embedding_literal=literal,
        metadata={},
        acl={},
        nivel_seguridad=nivel,
    )


def _request(tenant_slug: str) -> Request:
    return Request({"type": "http", "headers": [(b"x-tenant-slug", tenant_slug.encode())]})


class _RecordingProvider:
    model_id = "fake-integration-model"

    def __init__(self, *, raise_unavailable: str | None = None) -> None:
        self._raise = raise_unavailable
        self.received_chunks: list[RetrievedChunk] | None = None

    def generate_grounded(self, query: str, chunks: list[RetrievedChunk]) -> GeneratedAnswer:
        self.received_chunks = list(chunks)
        if self._raise is not None:
            raise RAGUnavailableError(self._raise)
        return GeneratedAnswer(answer="respuesta anclada", citations=[], model_id=self.model_id)


async def _high_clearance(_conn: asyncpg.Connection, _user_id: uuid.UUID | None) -> int:
    """Sustituye `resolve_caller_clearance` para simular un llamante con
    clearance máximo SIN depender de las tablas `auth_users`/`auth_memberships`
    (que no viven en el tenant desechable de `tenant_ctx`) — así se puede
    aislar la barrera dura (capa 2) de la capa ACL (capa 1): con clearance
    CLASIFICADA, la capa 1 deja pasar TODO, y lo único que puede excluir un
    chunk reservado es `is_generatable`."""
    return CLASIFICADA


# --- (a) barrera dura is_generatable, capa 2 (aislada de la capa ACL) --------------


@pytest.mark.integration
async def test_hard_barrier_excludes_reserved_chunk_from_external_provider(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, conn = tenant_ctx
    texto = "trámite de licencia de construcción"
    await _seed(conn, source_ref="PUB-1", texto=texto, nivel=PUBLICA)
    await _seed(conn, source_ref="RES-1", texto=texto + " version reservada", nivel=RESERVADA)

    monkeypatch.setattr(cfg_module.settings, "ai_provider", "anthropic")  # EXTERNO
    provider = _RecordingProvider()
    monkeypatch.setattr(knowledge_router, "get_llm_provider", lambda name=None: provider)
    monkeypatch.setattr(knowledge_router, "resolve_caller_clearance", _high_clearance)

    resp = await rag(
        RagRequest(query=texto, top_k=10),
        request=_request(slug),
        conn=conn,
        caller_id=None,
        canal="system",
    )

    assert resp.proveedor == "anthropic"
    assert provider.received_chunks is not None
    received_refs = {c.source_ref for c in provider.received_chunks}
    assert "PUB-1" in received_refs
    assert "RES-1" not in received_refs, (
        "CRÍTICO: un chunk nivel>=RESERVADA leído de Postgres real NUNCA debe "
        "llegar al LLMProvider cuando AI_PROVIDER es externo, aunque el "
        "clearance del llamante SÍ lo autorice a VERLO (capa ACL separada)"
    )


@pytest.mark.integration
async def test_hard_barrier_includes_reserved_chunk_for_local_provider(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gemelo: con proveedor LOCAL, el mismo chunk RESERVADA SÍ compone el
    contexto — la barrera dura discrimina por PROVEEDOR, no por nivel a secas."""
    slug, conn = tenant_ctx
    texto = "trámite de licencia ambiental"
    await _seed(conn, source_ref="RES-2", texto=texto, nivel=RESERVADA)

    monkeypatch.setattr(cfg_module.settings, "ai_provider", "ollama_local")  # LOCAL
    provider = _RecordingProvider()
    monkeypatch.setattr(knowledge_router, "get_llm_provider", lambda name=None: provider)
    monkeypatch.setattr(knowledge_router, "resolve_caller_clearance", _high_clearance)

    resp = await rag(
        RagRequest(query=texto, top_k=10),
        request=_request(slug),
        conn=conn,
        caller_id=None,
        canal="system",
    )

    assert resp.proveedor == "ollama_local"
    received_refs = {c.source_ref for c in provider.received_chunks}
    assert "RES-2" in received_refs, "con proveedor LOCAL, el material reservado SÍ compone el contexto"


# --- (b) pre-filtro ACL, capa 1 (motor real, antes de la barrera dura) -------------


@pytest.mark.integration
async def test_acl_prefilter_excludes_classified_chunk_before_any_barrier_runs(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Con clearance PUBLICA (caller_id=None -> `resolve_caller_clearance` REAL,
    SIN monkeypatch) un chunk CLASIFICADA queda excluido de `rows` por el
    `WHERE` de `search()` — INCLUSO con proveedor LOCAL (que si el chunk
    llegara a `rows`, no lo excluiría por residencia): si esta prueba fallara,
    demostraría que el pre-filtro ACL, no la barrera dura, es el que falta."""
    slug, conn = tenant_ctx
    texto = "solicitud de copia de expediente"
    await _seed(conn, source_ref="PUB-3", texto=texto, nivel=PUBLICA)
    await _seed(conn, source_ref="CLA-3", texto=texto + " version clasificada", nivel=CLASIFICADA)

    monkeypatch.setattr(cfg_module.settings, "ai_provider", "ollama_local")
    provider = _RecordingProvider()
    monkeypatch.setattr(knowledge_router, "get_llm_provider", lambda name=None: provider)

    resp = await rag(
        RagRequest(query=texto, top_k=10),
        request=_request(slug),
        conn=conn,
        caller_id=None,  # clearance PUBLICA real, sin overrides
        canal="system",
    )

    assert resp.proveedor == "ollama_local"
    received_refs = {c.source_ref for c in provider.received_chunks}
    assert "PUB-3" in received_refs
    assert "CLA-3" not in received_refs, (
        "el pre-filtro ACL (capa 1, SQL real) debe excluir un chunk CLASIFICADA "
        "del clearance PUBLICA del llamante — con proveedor LOCAL, si esto "
        "fallara el chunk SÍ llegaría igualmente al LLM"
    )


# --- (c) kb_suggestions + audit_log (proveedor/modelo separados, sin contenido) ----


@pytest.mark.integration
async def test_kb_suggestion_persisted_with_separate_columns_and_audited_without_content(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, conn = tenant_ctx
    texto = "trámite de embargo"
    await _seed(conn, source_ref="PUB-4", texto=texto, nivel=PUBLICA)

    monkeypatch.setattr(cfg_module.settings, "ai_provider", "ollama_local")
    provider = _RecordingProvider()
    monkeypatch.setattr(knowledge_router, "get_llm_provider", lambda name=None: provider)

    caller_id = uuid.uuid4()
    resp = await rag(
        RagRequest(query=texto, top_k=5),
        request=_request(slug),
        conn=conn,
        caller_id=caller_id,
        canal="ui",
    )

    assert resp.suggestion_id is not None
    row = await conn.fetchrow(
        "SELECT proveedor, modelo, confianza, canal, aceptada_por, aceptada_en "
        "FROM kb_suggestions WHERE id = $1",
        resp.suggestion_id,
    )
    assert row is not None
    assert row["proveedor"] == "ollama_local"
    assert row["modelo"] == provider.model_id
    assert row["canal"] == "ui"
    assert row["aceptada_por"] is None
    assert row["aceptada_en"] is None

    audit_row = await conn.fetchrow(
        "SELECT action, object_type, object_ref, payload, actor, canal "
        "FROM public.audit_log WHERE tenant_slug = $1 AND action = 'rag.generado' "
        "ORDER BY id DESC LIMIT 1",
        slug,
    )
    assert audit_row is not None
    assert audit_row["object_type"] == "kb_suggestion"
    assert audit_row["object_ref"] == str(resp.suggestion_id)
    assert audit_row["actor"] == str(caller_id)
    assert audit_row["canal"] == "ui"
    payload = json.loads(audit_row["payload"])
    assert set(payload) == {"proveedor", "modelo", "confianza"}, (
        "el asiento de audit_log NO debe llevar el contenido de la respuesta "
        "(query/answer/citations) — solo proveedor+modelo+confianza (CC-03)"
    )
    assert payload["proveedor"] == "ollama_local"
    assert payload["modelo"] == provider.model_id


@pytest.mark.integration
async def test_kb_suggestions_isolated_by_tenant(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch: pytest.MonkeyPatch
) -> None:
    slug_a, conn_a = tenant_ctx
    texto = "trámite de licencia de funcionamiento"
    await _seed(conn_a, source_ref="PUB-5", texto=texto, nivel=PUBLICA)

    monkeypatch.setattr(cfg_module.settings, "ai_provider", "ollama_local")
    provider = _RecordingProvider()
    monkeypatch.setattr(knowledge_router, "get_llm_provider", lambda name=None: provider)

    resp_a = await rag(
        RagRequest(query=texto, top_k=5),
        request=_request(slug_a),
        conn=conn_a,
        caller_id=None,
        canal="system",
    )
    assert resp_a.suggestion_id is not None

    slug_b = _make_slug("rag-tenant-b")
    schema_b = tenant_schema(slug_b)
    conn_b = await asyncpg.connect(dsn=DSN)
    try:
        await conn_b.execute(f'CREATE SCHEMA "{schema_b}"')
        for path in runner._sql_files(runner.TENANT_DIR):
            async with conn_b.transaction():
                await conn_b.execute(f'SET LOCAL search_path = "{schema_b}", public')
                await conn_b.execute(path.read_text())
        await conn_b.execute(f'SET search_path = "{schema_b}", public')

        row_from_b = await conn_b.fetchrow(
            "SELECT id FROM kb_suggestions WHERE id = $1", resp_a.suggestion_id
        )
        assert row_from_b is None, (
            "kb_suggestions vive en el schema del tenant — una fila del tenant A "
            "nunca debe ser visible desde el search_path del tenant B"
        )
        count_b = await conn_b.fetchval("SELECT count(*) FROM kb_suggestions")
        assert count_b == 0
    finally:
        assert schema_b.startswith("tenant_itest_")
        await conn_b.execute(f'DROP SCHEMA IF EXISTS "{schema_b}" CASCADE')
        await conn_b.close()


# --- (e) fallo del provider: sin efectos secundarios parciales ---------------------


@pytest.mark.integration
async def test_provider_failure_writes_no_kb_suggestion_row(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, conn = tenant_ctx
    texto = "trámite que falla"
    await _seed(conn, source_ref="PUB-6", texto=texto, nivel=PUBLICA)

    monkeypatch.setattr(cfg_module.settings, "ai_provider", "ollama_local")
    provider = _RecordingProvider(raise_unavailable="proveedor caído en integración")
    monkeypatch.setattr(knowledge_router, "get_llm_provider", lambda name=None: provider)

    with pytest.raises(HTTPException) as exc:
        await rag(
            RagRequest(query=texto, top_k=5),
            request=_request(slug),
            conn=conn,
            caller_id=None,
            canal="system",
        )

    assert exc.value.status_code == 503
    assert exc.value.detail["error"] == "rag_unavailable"
    count = await conn.fetchval("SELECT count(*) FROM kb_suggestions")
    assert count == 0


# --- (d) migración 008 idempotente --------------------------------------------------


@pytest.mark.integration
async def test_migration_008_kb_suggestions_is_idempotent(
    tenant_ctx: tuple[str, asyncpg.Connection],
) -> None:
    """`tenant_ctx` ya aplicó TODAS las migraciones de tenant (incl. 008) una
    vez al provisionar el schema. Re-aplicarla no debe fallar ni duplicar el
    constraint/los índices (`CREATE TABLE IF NOT EXISTS` + guard `IF NOT
    EXISTS` sobre el CHECK + `CREATE INDEX IF NOT EXISTS`)."""
    slug, conn = tenant_ctx
    schema = tenant_schema(slug)
    path_008 = next(
        p for p in runner._sql_files(runner.TENANT_DIR) if p.name == "008_kb_suggestions.sql"
    )

    async with conn.transaction():
        await conn.execute(f'SET LOCAL search_path = "{schema}", public')
        await conn.execute(path_008.read_text())

    constraint_count = await conn.fetchval(
        "SELECT count(*) FROM pg_constraint c "
        "JOIN pg_namespace n ON n.oid = c.connamespace "
        "WHERE c.conname = 'chk_kb_suggestions_canal' AND n.nspname = $1",
        schema,
    )
    assert constraint_count == 1

    created_at_idx = await conn.fetchval(
        "SELECT count(*) FROM pg_indexes WHERE schemaname = $1 AND indexname = 'ix_kb_suggestions_created_at'",
        schema,
    )
    assert created_at_idx == 1

    objeto_ref_idx = await conn.fetchval(
        "SELECT count(*) FROM pg_indexes WHERE schemaname = $1 AND indexname = 'ix_kb_suggestions_objeto_ref'",
        schema,
    )
    assert objeto_ref_idx == 1

    table_count = await conn.fetchval(
        "SELECT count(*) FROM information_schema.tables "
        "WHERE table_schema = $1 AND table_name = 'kb_suggestions'",
        schema,
    )
    assert table_count == 1
