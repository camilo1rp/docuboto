# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests unitarios de `POST /api/v1/knowledge/rag` (E21 Inc.3, Patrón B).

Cliente + conexión mockeada (``client_with_conn``, ver ``tests/conftest.py``) —
mismo patrón que ``test_antecedentes.py``. El `LLMProvider` se sustituye
SIEMPRE por `_RecordingProvider` (un doble en memoria, sin red ni SDK real):
`AI_PROVIDER` se fuerza a `disabled` a nivel de módulo por el conftest padre, y
cada test que ejercita la rama generativa monkeypatchea explícitamente
`ai_provider` a un nombre real Y sustituye `app.routers.knowledge.get_llm_provider`
— nunca se deja que la llamada llegue a `httpx`/al SDK real.

Cubre (mandato Inc.3):
  1. BARRERA DURA `is_generatable`: con proveedor EXTERNO, un chunk
     nivel>=RESERVADA recuperado por `search()` NUNCA llega al `LLMProvider`
     mockeado (aserción sobre los `chunks` que de verdad recibió). Con
     proveedor LOCAL, sí llega. Sin contexto generable tras la exclusión,
     degradación a Patrón A con TODO lo recuperado (visible por ACL).
  3. Degradación HONESTA (RF-FIR-15): fallo del provider -> 503
     `rag_unavailable`, nunca una respuesta simulada; sin fila en
     `kb_suggestions` ni asiento en `audit_log`.
  6. `kb_suggestions` (proveedor/modelo en columnas separadas + confianza +
     canal) y el asiento en `audit_log` (proveedor+modelo, SIN contenido).

El pre-filtro ACL real (capa 1, SQL) y el aislamiento por tenant de
`kb_suggestions`/`audit_log` requieren Postgres real — viven en
``tests/integration/test_rag_generation_barrier.py``. La contención de
inyección de prompt y el mapeo de citas por proveedor real viven en
``tests/test_llm.py`` (unidades de `app/services/llm.py`).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.routers.knowledge as knowledge_router
from app.core import config as cfg_module
from app.core.clearance import CLASIFICADA, PUBLICA, RESERVADA
from app.routers.knowledge import RAG_ADVISORY
from app.services.llm import Citation, GeneratedAnswer, RAGUnavailableError, RetrievedChunk
from tests.conftest import TENANT_HEADERS

NOW = datetime.now(tz=UTC)

URL = "/api/v1/knowledge/rag"


def _row(source_ref: str, *, texto: str = "texto", score: float = 0.5, nivel: int = PUBLICA) -> dict:
    return {
        "id": uuid.uuid4(),
        "source_type": "radicado",
        "source_ref": source_ref,
        "texto": texto,
        "metadata": {},
        "nivel_seguridad": nivel,
        "score": score,
    }


class _RecordingProvider:
    """Doble en memoria de `LLMProvider` — nunca hace red ni carga un SDK."""

    model_id = "fake-model-x"

    def __init__(
        self,
        *,
        answer: str = "respuesta anclada",
        citations: list[Citation] | None = None,
        raise_unavailable: str | None = None,
    ) -> None:
        self._answer = answer
        self._citations = citations or []
        self._raise = raise_unavailable
        self.received_query: str | None = None
        self.received_chunks: list[RetrievedChunk] | None = None
        self.call_count = 0

    def generate_grounded(self, query: str, chunks: list[RetrievedChunk]) -> GeneratedAnswer:
        self.call_count += 1
        self.received_query = query
        self.received_chunks = list(chunks)
        if self._raise is not None:
            raise RAGUnavailableError(self._raise)
        return GeneratedAnswer(answer=self._answer, citations=self._citations, model_id=self.model_id)


def _suggestion_row() -> dict:
    return {"id": uuid.uuid4(), "created_at": NOW}


def _patched_audit():
    """`patch("app.routers.knowledge.audit")` con `.append` ya como AsyncMock —
    mismo patrón que `test_ingestion.py`/`test_document_consumer.py`: se
    sustituye el módulo ENTERO, nunca se deja que `audit.append` real toque la
    conexión mockeada (que no soporta de verdad `async with conn.transaction()`)."""
    ctx = patch("app.routers.knowledge.audit")
    mock_audit = ctx.start()
    mock_audit.append = AsyncMock()
    return ctx, mock_audit


# --- Patrón A (AI_PROVIDER=disabled, default de módulo) ----------------------------


@pytest.mark.asyncio
async def test_disabled_provider_falls_back_to_patron_a_without_generating(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetch.return_value = [_row("2024-DEMO-E-000001", texto="contrato de obra", score=0.9)]

    resp = await client.post(URL, json={"query": "contratos de obra", "top_k": 5}, headers=TENANT_HEADERS)

    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] is None
    assert body["modelo"] is None
    assert body["confianza"] == 0.0
    assert body["suggestion_id"] is None
    assert body["proveedor"] == "disabled"
    assert body["citations"] == [
        {
            "cited_text": "contrato de obra",
            "source_type": "radicado",
            "source_ref": "2024-DEMO-E-000001",
            "score": 0.9,
        }
    ]
    assert body["advisory"] == RAG_ADVISORY
    # Patrón A: ni se registra sugerencia (fetchrow no se invoca para eso) ni se audita.
    assert conn.fetchrow.call_count == 0


@pytest.mark.asyncio
async def test_unregistered_provider_name_also_falls_back_reporting_disabled(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cfg_module.settings, "ai_provider", "algun-nombre-no-registrado")
    client, conn = client_with_conn
    conn.fetch.return_value = []

    resp = await client.post(URL, json={"query": "algo"}, headers=TENANT_HEADERS)

    assert resp.status_code == 200
    assert resp.json()["proveedor"] == "disabled"


# --- 1. BARRERA DURA is_generatable (CRÍTICO) ---------------------------------------


@pytest.mark.asyncio
async def test_hard_barrier_external_provider_never_receives_classified_chunks(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El invariante del epic: con un proveedor EXTERNO, los chunks
    nivel>=RESERVADA recuperados por `search()` se EXCLUYEN del contexto que
    llega al `LLMProvider` — se verifica por COMPORTAMIENTO, inspeccionando lo
    que `_RecordingProvider` recibió realmente."""
    monkeypatch.setattr(cfg_module.settings, "ai_provider", "anthropic")
    provider = _RecordingProvider(
        citations=[Citation(cited_text="frag", source_type="radicado", source_ref="PUB-1", score=0.9)]
    )
    monkeypatch.setattr(knowledge_router, "get_llm_provider", lambda name=None: provider)

    client, conn = client_with_conn
    conn.fetch.return_value = [
        _row("PUB-1", texto="público", nivel=PUBLICA),
        _row("RES-1", texto="reservado", nivel=RESERVADA),
        _row("CLA-1", texto="clasificado", nivel=CLASIFICADA),
    ]
    conn.fetchrow.return_value = _suggestion_row()

    ctx, _mock_audit = _patched_audit()
    try:
        resp = await client.post(URL, json={"query": "trámite", "top_k": 10}, headers=TENANT_HEADERS)
    finally:
        ctx.stop()

    assert resp.status_code == 200
    assert provider.received_chunks is not None
    received_refs = {c.source_ref for c in provider.received_chunks}
    assert received_refs == {"PUB-1"}, (
        "CRÍTICO: ningún chunk nivel>=RESERVADA debe llegar al proveedor EXTERNO "
        f"— recibió {received_refs!r}"
    )
    assert resp.json()["proveedor"] == "anthropic"


@pytest.mark.asyncio
async def test_hard_barrier_local_provider_includes_classified_chunks(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Gemelo del test anterior: con proveedor LOCAL (`ollama_local`), el
    material Reservado/Clasificado SÍ compone el contexto — la barrera dura
    solo actúa contra proveedores externos."""
    monkeypatch.setattr(cfg_module.settings, "ai_provider", "ollama_local")
    provider = _RecordingProvider()
    monkeypatch.setattr(knowledge_router, "get_llm_provider", lambda name=None: provider)

    client, conn = client_with_conn
    conn.fetch.return_value = [
        _row("PUB-2", texto="público", nivel=PUBLICA),
        _row("RES-2", texto="reservado", nivel=RESERVADA),
        _row("CLA-2", texto="clasificado", nivel=CLASIFICADA),
    ]
    conn.fetchrow.return_value = _suggestion_row()

    ctx, _mock_audit = _patched_audit()
    try:
        resp = await client.post(URL, json={"query": "trámite", "top_k": 10}, headers=TENANT_HEADERS)
    finally:
        ctx.stop()

    assert resp.status_code == 200
    received_refs = {c.source_ref for c in provider.received_chunks}
    assert received_refs == {"PUB-2", "RES-2", "CLA-2"}


@pytest.mark.asyncio
async def test_hard_barrier_no_context_left_falls_back_to_patron_a_with_all_visible_rows(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Si tras excluir lo clasificado no queda NADA generable, cae a Patrón A —
    pero con TODAS las filas que el usuario puede VER por ACL (`rows`), no solo
    las generables (`generatable_rows`): el límite es sobre qué sale al
    proveedor, no sobre qué puede consultar el usuario."""
    monkeypatch.setattr(cfg_module.settings, "ai_provider", "anthropic")
    get_provider_spy = MagicMock()
    monkeypatch.setattr(knowledge_router, "get_llm_provider", get_provider_spy)

    client, conn = client_with_conn
    conn.fetch.return_value = [_row("CLA-3", texto="solo clasificado", nivel=CLASIFICADA)]

    resp = await client.post(URL, json={"query": "trámite clasificado", "top_k": 5}, headers=TENANT_HEADERS)

    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] is None
    assert body["modelo"] is None
    assert body["suggestion_id"] is None
    assert body["proveedor"] == "anthropic"
    assert [c["source_ref"] for c in body["citations"]] == ["CLA-3"], (
        "el usuario SÍ puede VER el chunk clasificado (ya pasó el pre-filtro "
        "ACL) — el límite es únicamente sobre qué sale al proveedor externo"
    )
    get_provider_spy.assert_not_called()
    assert conn.fetchrow.call_count == 0, "sin generación real, no se registra kb_suggestions"


# --- 3. Degradación HONESTA (RF-FIR-15) ---------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_reason",
    [
        "AI_PROVIDER=anthropic requiere ANTHROPIC_API_KEY configurada",
        "proveedor anthropic no disponible: connection reset",
        "SDK 'anthropic' no está instalado en este despliegue",
    ],
)
async def test_provider_failure_is_always_503_never_a_simulated_answer(
    client_with_conn, monkeypatch: pytest.MonkeyPatch, failure_reason: str
) -> None:
    monkeypatch.setattr(cfg_module.settings, "ai_provider", "anthropic")
    provider = _RecordingProvider(raise_unavailable=failure_reason)
    monkeypatch.setattr(knowledge_router, "get_llm_provider", lambda name=None: provider)

    client, conn = client_with_conn
    conn.fetch.return_value = [_row("PUB-4", texto="público", nivel=PUBLICA)]

    resp = await client.post(URL, json={"query": "algo"}, headers=TENANT_HEADERS)

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "rag_unavailable"
    assert failure_reason in resp.json()["detail"]["detail"]
    # Ningún efecto secundario parcial: ni sugerencia ni auditoría.
    assert conn.fetchrow.call_count == 0


@pytest.mark.asyncio
async def test_provider_failure_writes_no_kb_suggestion_and_no_audit_entry(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cfg_module.settings, "ai_provider", "ollama_local")
    provider = _RecordingProvider(raise_unavailable="ollama caído")
    monkeypatch.setattr(knowledge_router, "get_llm_provider", lambda name=None: provider)

    client, conn = client_with_conn
    conn.fetch.return_value = [_row("PUB-5", nivel=PUBLICA)]

    ctx, mock_audit = _patched_audit()
    try:
        resp = await client.post(URL, json={"query": "algo"}, headers=TENANT_HEADERS)
        mock_audit.append.assert_not_called()
    finally:
        ctx.stop()

    assert resp.status_code == 503


# --- 6. kb_suggestions (proveedor/modelo separados) + audit_log --------------------


@pytest.mark.asyncio
async def test_successful_generation_persists_suggestion_with_separate_columns(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cfg_module.settings, "ai_provider", "ollama_local")
    citation = Citation(cited_text="frag citado", source_type="radicado", source_ref="PUB-6", score=0.77)
    provider = _RecordingProvider(answer="la respuesta generada", citations=[citation])
    monkeypatch.setattr(knowledge_router, "get_llm_provider", lambda name=None: provider)

    client, conn = client_with_conn
    conn.fetch.return_value = [_row("PUB-6", texto="público", nivel=PUBLICA)]
    suggestion_id = uuid.uuid4()
    conn.fetchrow.return_value = {"id": suggestion_id, "created_at": NOW}

    ctx, mock_audit = _patched_audit()
    try:
        resp = await client.post(
            URL,
            json={"query": "trámite público", "top_k": 5},
            headers={**TENANT_HEADERS, "X-Canal": "mcp", "X-User-Id": str(uuid.uuid4())},
        )
        mock_audit.append.assert_awaited_once()
        audit_kwargs = mock_audit.append.call_args.kwargs
    finally:
        ctx.stop()

    assert resp.status_code == 200
    body = resp.json()
    assert body["answer"] == "la respuesta generada"
    assert body["proveedor"] == "ollama_local"
    assert body["modelo"] == provider.model_id
    assert body["suggestion_id"] == str(suggestion_id)
    assert body["confianza"] == 1.0  # 1 chunk enviado, 1 citación distinta -> 1/1

    # KbSuggestionRepository.insert: (sql, tipo, objeto_ref, sugerencia_json,
    # proveedor, modelo, confianza, canal) — proveedor/modelo en columnas
    # SEPARADAS (CC-03), no embebidos dentro del JSONB `sugerencia`.
    insert_args = conn.fetchrow.call_args.args
    assert insert_args[1] == "rag_respuesta"
    assert insert_args[2] is None  # objeto_ref
    assert insert_args[4] == "ollama_local"  # proveedor
    assert insert_args[5] == provider.model_id  # modelo
    assert insert_args[6] == 1.0  # confianza
    assert insert_args[7] == "mcp"  # canal (propagado desde X-Canal)

    # audit_log: proveedor + modelo + confianza, SIN el contenido (query/answer/citations).
    assert audit_kwargs["action"] == "rag.generado"
    assert audit_kwargs["object_type"] == "kb_suggestion"
    assert audit_kwargs["object_ref"] == str(suggestion_id)
    assert audit_kwargs["canal"] == "mcp"
    assert set(audit_kwargs["payload"]) == {"proveedor", "modelo", "confianza"}
    assert audit_kwargs["payload"]["proveedor"] == "ollama_local"
    assert audit_kwargs["payload"]["modelo"] == provider.model_id


@pytest.mark.asyncio
async def test_confianza_is_fraction_of_context_chunks_actually_cited(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cfg_module.settings, "ai_provider", "ollama_local")
    # 4 chunks recuperados (todos generables, proveedor local); solo 2 fuentes
    # DISTINTAS terminan citadas -> confianza = 2/4 = 0.5.
    citations = [
        Citation(cited_text="a", source_type="radicado", source_ref="C-1", score=0.9),
        Citation(cited_text="b", source_type="radicado", source_ref="C-2", score=0.8),
    ]
    provider = _RecordingProvider(citations=citations)
    monkeypatch.setattr(knowledge_router, "get_llm_provider", lambda name=None: provider)

    client, conn = client_with_conn
    conn.fetch.return_value = [_row(f"C-{i}", nivel=PUBLICA) for i in range(1, 5)]
    conn.fetchrow.return_value = _suggestion_row()

    ctx, _mock_audit = _patched_audit()
    try:
        resp = await client.post(URL, json={"query": "algo", "top_k": 4}, headers=TENANT_HEADERS)
    finally:
        ctx.stop()

    assert resp.status_code == 200
    assert resp.json()["confianza"] == 0.5


@pytest.mark.asyncio
async def test_default_canal_is_system_without_x_canal_header(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cfg_module.settings, "ai_provider", "ollama_local")
    provider = _RecordingProvider()
    monkeypatch.setattr(knowledge_router, "get_llm_provider", lambda name=None: provider)

    client, conn = client_with_conn
    conn.fetch.return_value = [_row("PUB-7", nivel=PUBLICA)]
    conn.fetchrow.return_value = _suggestion_row()

    ctx, mock_audit = _patched_audit()
    try:
        await client.post(URL, json={"query": "algo"}, headers=TENANT_HEADERS)
        canal_arg = mock_audit.append.call_args.kwargs["canal"]
    finally:
        ctx.stop()

    assert canal_arg == "system"
    assert conn.fetchrow.call_args.args[7] == "system"


# --- Recuperación: max_clearance/top_k pasados tal cual a search() -----------------


@pytest.mark.asyncio
async def test_search_receives_configured_top_k_and_public_clearance_without_user_id(
    client_with_conn,
) -> None:
    client, conn = client_with_conn
    conn.fetch.return_value = []

    resp = await client.post(URL, json={"query": "sin usuario", "top_k": 7}, headers=TENANT_HEADERS)

    assert resp.status_code == 200
    call = conn.fetch.call_args.args
    assert call[2] == 1  # max_clearance = PUBLICA, sin X-User-Id
    assert call[3] == 7  # top_k tal cual (RagRequest no pide +1, no hay pivote)
