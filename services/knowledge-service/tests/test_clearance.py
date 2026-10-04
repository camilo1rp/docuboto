# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests unitarios del predicado puro `app/core/clearance.py` (D-02/RF-SEG-08),
cobertura NUEVA de E21 Inc.1: la semántica de residencia gana un tercer
proveedor local (`local_st`, sentence-transformers en el propio proceso) junto
a `stub` — ambos "no exfiltran" el texto, así que el material Reservado/
Clasificado SÍ puede embeberse con cualquiera de los dos. `ollama` (HTTP a una
instancia potencialmente remota) permanece fuera de `LOCAL_EMBEDDING_PROVIDERS`
deliberadamente (ver docstring del módulo) — se trata como externo hasta que
exista una reconciliación explícita de residencia (CC-03, diferido).

Estos tests NO dependen de ninguna implementación real de proveedor (no
importan `embeddings.py`): ejercitan el predicado puro por nombre de cadena,
que es exactamente lo que `is_embeddable`/`is_local_embedding_provider` reciben
en producción (`settings.embedding_provider`, un `str`).
"""

from __future__ import annotations

import pytest

from app.core.clearance import (
    CLASIFICADA,
    PUBLICA,
    RESERVADA,
    is_embeddable,
    is_generatable,
    is_local_embedding_provider,
    is_local_llm_provider,
)


class TestIsLocalEmbeddingProvider:
    @pytest.mark.parametrize("provider", ["stub", "local_st"])
    def test_local_providers(self, provider: str) -> None:
        assert is_local_embedding_provider(provider) is True

    @pytest.mark.parametrize("provider", ["ollama", "openai", "external", "", "STUB", "Local_ST"])
    def test_non_local_providers(self, provider: str) -> None:
        """`ollama` es EXTERNO a propósito (instancia potencialmente remota, ver
        docstring del módulo) — junto con cualquier nombre desconocido/mal
        escrito. La comparación es exacta (case-sensitive): 'STUB'/'Local_ST'
        NO cuentan como locales — fail-closed ante un typo de configuración."""
        assert is_local_embedding_provider(provider) is False


class TestIsEmbeddable:
    @pytest.mark.parametrize("provider", ["stub", "local_st"])
    @pytest.mark.parametrize("nivel", [RESERVADA, CLASIFICADA])
    def test_clasificado_con_proveedor_local_es_embebible(self, nivel: int, provider: str) -> None:
        """Núcleo de E21 Inc.1: con un proveedor LOCAL/soberano, material
        Reservado/Clasificado SÍ puede embeberse — el texto nunca sale del
        proceso/contenedor del servicio. Antes de este incremento, ningún
        proveedor real (más allá del stub de test) tenía esta propiedad."""
        assert is_embeddable(nivel, provider) is True

    @pytest.mark.parametrize("provider", ["ollama", "openai", "external"])
    @pytest.mark.parametrize("nivel", [RESERVADA, CLASIFICADA])
    def test_clasificado_con_proveedor_externo_no_es_embebible(
        self, nivel: int, provider: str
    ) -> None:
        """Fail-closed sin cambios: material Reservado/Clasificado sigue
        bloqueado con cualquier proveedor no-local, `ollama` incluido (no se
        confía en que la instancia configurada sea, de hecho, local)."""
        assert is_embeddable(nivel, provider) is False

    @pytest.mark.parametrize("provider", ["stub", "local_st", "ollama", "openai", "external"])
    def test_publica_es_siempre_embebible(self, provider: str) -> None:
        """Bajo RESERVADA, la residencia del proveedor es irrelevante — material
        Público se embebe con cualquier proveedor configurado."""
        assert is_embeddable(PUBLICA, provider) is True


class TestIsLocalLlmProvider:
    """E21 Inc.3 — gemelo de `TestIsLocalEmbeddingProvider`, pero para
    GENERACIÓN. `LOCAL_LLM_PROVIDERS` es un conjunto de UN solo nombre
    (`ollama_local`) — a diferencia de embeddings, `ollama_cloud` (mismo shape
    de API, endpoint EXTERNO) nunca se confunde con `ollama_local`."""

    def test_ollama_local_is_local(self) -> None:
        assert is_local_llm_provider("ollama_local") is True

    @pytest.mark.parametrize(
        "provider",
        ["ollama_cloud", "openai_compatible", "anthropic", "ollama", "disabled", "", "Ollama_Local"],
    )
    def test_every_other_provider_is_external(self, provider: str) -> None:
        """`ollama_cloud` (mismo shape HTTP que `ollama_local`, pero endpoint
        externo) y `disabled` (nunca invoca este predicado en producción, ver
        docstring del módulo) quedan fuera a propósito — igual que un typo de
        capitalización (fail-closed, comparación exacta)."""
        assert is_local_llm_provider(provider) is False


class TestIsGeneratable:
    """Barrera dura de soberanía (E21 Inc.3, decisión TOMADA 2026-07-20): a
    diferencia de `is_embeddable`, NO existe override configurable — el
    predicado no tiene ninguna rama que permita excepción."""

    @pytest.mark.parametrize("nivel", [RESERVADA, CLASIFICADA])
    def test_clasificado_con_proveedor_local_es_generable(self, nivel: int) -> None:
        assert is_generatable(nivel, "ollama_local") is True

    @pytest.mark.parametrize("nivel", [RESERVADA, CLASIFICADA])
    @pytest.mark.parametrize("provider", ["ollama_cloud", "openai_compatible", "anthropic"])
    def test_clasificado_con_proveedor_externo_nunca_es_generable(
        self, nivel: int, provider: str
    ) -> None:
        """CRÍTICO: núcleo del invariante del epic — material Reservado/
        Clasificado NUNCA puede salir a un LLM externo, sin excepción."""
        assert is_generatable(nivel, provider) is False

    @pytest.mark.parametrize(
        "provider", ["ollama_local", "ollama_cloud", "openai_compatible", "anthropic", "disabled"]
    )
    def test_publica_es_siempre_generable(self, provider: str) -> None:
        assert is_generatable(PUBLICA, provider) is True


class TestOllamaLocalDependeDelHost:
    """AREA-11-12: `ollama_local` es soberano por su DESTINO, no por su nombre.

    Con `AI_PROVIDER=ollama_local` y una `OLLAMA_URL` pública, el RAG enviaba
    chunks nivel 2-3 a ese host y solo quedaba un warning en el log.
    """

    @pytest.mark.parametrize("nivel", [RESERVADA, CLASIFICADA])
    @pytest.mark.parametrize(
        "url",
        [
            "https://ollama.example.com",
            "http://ollama.example.com:11434",
            "http://8.8.8.8:11434",
            "",  # sin host: fail-closed
        ],
    )
    def test_host_publico_no_es_generable_en_nivel_2_o_3(self, nivel: int, url: str) -> None:
        assert is_generatable(nivel, "ollama_local", url) is False
        assert is_local_llm_provider("ollama_local", url) is False

    @pytest.mark.parametrize("nivel", [RESERVADA, CLASIFICADA])
    @pytest.mark.parametrize(
        "url",
        [
            "http://ollama:11434",  # control positivo: hostname de contenedor
            "http://localhost:11434",
            "http://127.0.0.1:11434",
            "http://10.1.2.3:11434",
            "http://[::1]:11434",
            "http://gpu.entidad.local:11434",
        ],
    )
    def test_host_local_si_es_generable(self, nivel: int, url: str) -> None:
        assert is_generatable(nivel, "ollama_local", url) is True

    def test_nivel_publico_sigue_saliendo_con_host_publico(self) -> None:
        # La barrera es solo para >= RESERVADA: lo público no es material protegido.
        assert is_generatable(PUBLICA, "ollama_local", "https://ollama.example.com") is True

    def test_sin_url_explicita_se_lee_la_de_la_configuracion(self, monkeypatch) -> None:
        from app.core.config import settings

        monkeypatch.setattr(settings, "ollama_url", "https://ollama.example.com")
        assert is_generatable(RESERVADA, "ollama_local") is False
        monkeypatch.setattr(settings, "ollama_url", "http://ollama:11434")
        assert is_generatable(RESERVADA, "ollama_local") is True
