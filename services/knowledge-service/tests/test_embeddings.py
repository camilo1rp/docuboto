# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests unitarios de `app/services/embeddings.py` (E21 Inc.1: proveedor de
embeddings REAL) + el guard fail-closed de arranque de `app/main.py`.

NUNCA descarga/carga `sentence_transformers` de verdad — `LocalSentenceTransformerProvider`
se ejercita inyectando un modelo FALSO vía monkeypatch de `sys.modules["sentence_transformers"]`
(el import es perezoso, dentro de `_ensure_loaded`, así que basta con que el
módulo exista en `sys.modules` en el momento en que se llama). `tests/conftest.py`
ya fuerza `EMBEDDING_PROVIDER=stub` a nivel de sesión — estos tests NO dependen de
ese valor por defecto porque construyen instancias directamente (`_build_provider`,
`StubEmbeddingProvider`, `LocalSentenceTransformerProvider`), sin pasar por el
proveedor global salvo donde se indica explícitamente.

Cobertura:
  - `StubEmbeddingProvider`: determinista, dimensión correcta, `embed_documents`
    == `[embed_query(t) for t in texts]`.
  - `LocalSentenceTransformerProvider`: carga LAZY (nunca al construir), cacheada
    (una sola carga del modelo tras varios accesos), `.dim`/`.model_id` correctos,
    `embed_query`/`embed_documents` delegan al modelo fake.
  - `_build_provider`/`get_provider`: nombre desconocido → `ValueError` explícito
    (el punto que usan el guard de arranque y `app/ops/reembed.py` para fallar
    RÁPIDO ante un `EMBEDDING_PROVIDER` mal escrito).
  - `get_provider` cachea por nombre (una sola instancia de proceso).
  - Guard fail-closed `app/main.py::_assert_embedding_dim_matches_provider`: un
    proveedor cuya `.dim` no coincide con `EMBEDDING_DIM` aborta el arranque
    (`RuntimeError`) ANTES de aceptar tráfico; si coincide, no lanza.
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock

import pytest

from app.core import config as cfg_module
from app.services import embeddings as emb_module
from app.services.embeddings import (
    StubEmbeddingProvider,
    _build_provider,
    get_provider,
)

TEXT_A = "Solicitud de prueba número uno"
TEXT_B = "Otro documento completamente distinto"


# ---------------------------------------------------------------------------
# StubEmbeddingProvider
# ---------------------------------------------------------------------------


class TestStubEmbeddingProvider:
    def test_dim_default_reads_settings(self) -> None:
        provider = StubEmbeddingProvider()
        assert provider.dim == cfg_module.settings.embedding_dim

    def test_dim_can_be_fixed_explicitly(self) -> None:
        provider = StubEmbeddingProvider(dim=16)
        assert provider.dim == 16
        vec = provider.embed_query(TEXT_A)
        assert len(vec) == 16

    def test_embed_query_is_deterministic(self) -> None:
        provider = StubEmbeddingProvider(dim=32)
        assert provider.embed_query(TEXT_A) == provider.embed_query(TEXT_A)

    def test_embed_query_differs_by_text(self) -> None:
        provider = StubEmbeddingProvider(dim=32)
        assert provider.embed_query(TEXT_A) != provider.embed_query(TEXT_B)

    def test_embed_documents_matches_per_item_embed_query(self) -> None:
        provider = StubEmbeddingProvider(dim=32)
        texts = [TEXT_A, TEXT_B]
        assert provider.embed_documents(texts) == [provider.embed_query(t) for t in texts]

    def test_model_id_is_stable(self) -> None:
        assert StubEmbeddingProvider().model_id == "stub-hash-v1"


# ---------------------------------------------------------------------------
# LocalSentenceTransformerProvider — modelo FALSO, nunca sentence_transformers real
# ---------------------------------------------------------------------------


class _FakeSentenceTransformer:
    """Gemelo mínimo de `sentence_transformers.SentenceTransformer` — cuenta
    cuántas veces se instancia (para verificar la carga LAZY + cacheada)."""

    instances_created = 0

    def __init__(self, model_id: str) -> None:
        type(self).instances_created += 1
        self.model_id = model_id

    def get_sentence_embedding_dimension(self) -> int:
        return 384

    def encode(self, texts, normalize_embeddings: bool = True):  # noqa: ANN001
        single = isinstance(texts, str)
        batch = [texts] if single else list(texts)
        vecs = [[float(len(t))] * 384 for t in batch]
        result = MagicMock()
        result.tolist.return_value = vecs[0] if single else vecs
        return result


@pytest.fixture
def fake_sentence_transformers(monkeypatch: pytest.MonkeyPatch):
    """Inyecta un módulo `sentence_transformers` FALSO en `sys.modules` — el
    import perezoso de `_ensure_loaded` (`from sentence_transformers import
    SentenceTransformer`) lo resuelve desde aquí sin tocar la red ni disco."""
    _FakeSentenceTransformer.instances_created = 0
    fake_module = types.ModuleType("sentence_transformers")
    fake_module.SentenceTransformer = _FakeSentenceTransformer  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentence_transformers", fake_module)
    yield _FakeSentenceTransformer


class TestLocalSentenceTransformerProvider:
    def test_construction_does_not_load_model(self, fake_sentence_transformers) -> None:
        emb_module.LocalSentenceTransformerProvider(model_id="fake-model")
        assert fake_sentence_transformers.instances_created == 0

    def test_dim_triggers_lazy_load_exactly_once(self, fake_sentence_transformers) -> None:
        provider = emb_module.LocalSentenceTransformerProvider(model_id="fake-model")
        assert provider.dim == 384
        assert provider.dim == 384  # segundo acceso: NO recarga
        assert fake_sentence_transformers.instances_created == 1

    def test_embed_query_and_embed_documents_use_the_cached_model(
        self, fake_sentence_transformers
    ) -> None:
        provider = emb_module.LocalSentenceTransformerProvider(model_id="fake-model")
        vec = provider.embed_query(TEXT_A)
        assert len(vec) == 384

        vecs = provider.embed_documents([TEXT_A, TEXT_B])
        assert len(vecs) == 2
        assert all(len(v) == 384 for v in vecs)
        # embed_query + embed_documents comparten el modelo cacheado (una sola carga).
        assert fake_sentence_transformers.instances_created == 1

    def test_embed_documents_of_empty_list_short_circuits(self, fake_sentence_transformers) -> None:
        provider = emb_module.LocalSentenceTransformerProvider(model_id="fake-model")
        assert provider.embed_documents([]) == []
        # No debe haber cargado el modelo solo para devolver [].
        assert fake_sentence_transformers.instances_created == 0

    def test_model_id_is_the_configured_one(self, fake_sentence_transformers) -> None:
        provider = emb_module.LocalSentenceTransformerProvider(model_id="fake-model-xyz")
        assert provider.model_id == "fake-model-xyz"


# ---------------------------------------------------------------------------
# _build_provider / get_provider — nombre desconocido rechazado, cacheo
# ---------------------------------------------------------------------------


class TestBuildAndGetProvider:
    def test_build_provider_stub(self) -> None:
        assert isinstance(_build_provider("stub"), StubEmbeddingProvider)

    def test_build_provider_local_st(self) -> None:
        provider = _build_provider("local_st")
        assert isinstance(provider, emb_module.LocalSentenceTransformerProvider)
        assert provider.model_id == cfg_module.settings.model_embed

    def test_build_provider_ollama(self) -> None:
        provider = _build_provider("ollama")
        assert isinstance(provider, emb_module.OllamaEmbeddingProvider)

    @pytest.mark.parametrize("bad_name", ["openai", "external", "", "Stub", "LOCAL_ST"])
    def test_build_provider_rejects_unknown_names(self, bad_name: str) -> None:
        """Estricto a propósito: el guard de arranque (`app/main.py::lifespan`) y
        `app/ops/reembed.py` dependen de que esto falle RÁPIDO ante un
        `EMBEDDING_PROVIDER` mal escrito, antes de tocar ninguna fila."""
        with pytest.raises(ValueError, match="desconocido"):
            _build_provider(bad_name)

    def test_get_provider_caches_by_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        cache: dict = {}
        monkeypatch.setattr(emb_module, "_provider_cache", cache)
        first = get_provider("stub")
        second = get_provider("stub")
        assert first is second
        assert cache == {"stub": first}

    def test_get_provider_defaults_to_settings_embedding_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(emb_module, "_provider_cache", {})
        monkeypatch.setattr(cfg_module.settings, "embedding_provider", "stub")
        provider = get_provider()
        assert isinstance(provider, StubEmbeddingProvider)


# ---------------------------------------------------------------------------
# Guard fail-closed de arranque: app/main.py::_assert_embedding_dim_matches_provider
# ---------------------------------------------------------------------------


class _FakeProvider:
    def __init__(self, dim: int, model_id: str = "fake") -> None:
        self.dim = dim
        self.model_id = model_id


class TestStartupDimGuard:
    def test_matching_dim_does_not_raise(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import app.main as main_module

        monkeypatch.setattr(cfg_module.settings, "embedding_dim", 384)
        monkeypatch.setattr(main_module, "get_provider", lambda: _FakeProvider(dim=384))
        main_module._assert_embedding_dim_matches_provider()  # no debe lanzar

    def test_mismatched_dim_aborts_startup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """El caso central de E21 Inc.1: un proveedor que produce una dimensión
        distinta de `EMBEDDING_DIM` (columna `vector(EMBEDDING_DIM)`, migración
        007) NUNCA debe aceptar tráfico — aborta el proceso al arrancar en vez
        de fallar tarde y disperso en cada INSERT/UPSERT."""
        import app.main as main_module

        monkeypatch.setattr(cfg_module.settings, "embedding_dim", 384)
        monkeypatch.setattr(main_module, "get_provider", lambda: _FakeProvider(dim=64))
        with pytest.raises(RuntimeError, match="Arranque abortado"):
            main_module._assert_embedding_dim_matches_provider()
