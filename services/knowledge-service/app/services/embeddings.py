# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Proveedor de embeddings pluggable (E21 Inc.1, ADR-006).

Tres proveedores, seleccionados por `EMBEDDING_PROVIDER`:
  - `local_st` (DEFAULT de despliegue): `sentence-transformers` multilingüe,
    corre EN el contenedor del servicio — soberano, el texto nunca sale a un
    tercero. Carga LAZY (nunca al import; el modelo pesa ~400MB) y CACHEADA
    (una sola instancia de proceso, ver `get_provider`).
  - `stub`: el determinista original (hash de tokens, sin modelo). Fallback de
    test/dev — los tests fuerzan este proveedor a nivel de sesión
    (`tests/conftest.py`) para no descargar nunca un modelo en CI.
  - `ollama`: MÍNIMO VIABLE (petición HTTP a `{OLLAMA_URL}/api/embeddings`);
    no hay infraestructura Ollama provisionada en `docker-compose.yml` de este
    repo — la interfaz queda lista y funcional si el operador despliega un
    Ollama aparte. NO se considera proveedor local/soberano por defecto (ver
    `app/core/clearance.py::LOCAL_EMBEDDING_PROVIDERS`): la instancia puede ser
    remota, así que el gate de residencia lo trata como externo.

`EMBEDDING_DIM` (config) es la fuente ÚNICA de la dimensión de la columna
`knowledge_chunk.embedding` (una sola columna/índice ANN por schema). El
provider seleccionado DEBE producir vectores de esa dimensión — lo verifica el
guard fail-closed de `app/main.py::lifespan` al arrancar (compara
`get_provider().dim` contra `settings.embedding_dim` antes de aceptar tráfico).

`embed(text, dim=None)` conserva su firma histórica por compatibilidad: si se
pasa `dim` explícito (como hace el test unitario del propio stub) siempre usa
el algoritmo determinista sin pasar por el proveedor configurado; sin `dim`
(el caso real, `ingestion.py`/`routers/knowledge.py`) despacha al proveedor
seleccionado por `EMBEDDING_PROVIDER`. Así ni el import ni el patch-target de
los tests existentes (`app.services.ingestion.embed`) cambian.
"""

from __future__ import annotations

import hashlib
import math
import re
from abc import ABC, abstractmethod
from typing import Any

from app.core.config import settings


def _tokens(text: str) -> list[str]:
    return re.findall(r"\w+", text.lower())


def _stub_embed(text: str, dim: int) -> list[float]:
    vec = [0.0] * dim
    for tok in _tokens(text):
        h = int(hashlib.sha256(tok.encode()).hexdigest(), 16)
        idx = h % dim
        sign = 1.0 if (h >> 8) & 1 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        return vec
    return [v / norm for v in vec]


def to_pgvector(vec: list[float]) -> str:
    """Serializa a literal pgvector: '[v1,v2,...]'."""
    return "[" + ",".join(f"{v:.6f}" for v in vec) + "]"


class EmbeddingProvider(ABC):
    """Contrato pluggable de embeddings (E21 Inc.1)."""

    model_id: str

    @property
    @abstractmethod
    def dim(self) -> int:
        """Dimensión de los vectores que produce ESTE proveedor/modelo."""

    @abstractmethod
    def embed_query(self, text: str) -> list[float]:
        """Embebe un texto de consulta (búsqueda)."""

    @abstractmethod
    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embebe un lote de textos (ingesta/backfill) — permite batching real
        en los proveedores que lo soportan (`local_st`); el default aquí es
        `[embed_query(t) for t in texts]`, correcto pero sin batching."""
        return [self.embed_query(t) for t in texts]


class StubEmbeddingProvider(EmbeddingProvider):
    """Determinista, sin modelo — gemelo objeto de la función histórica `embed`."""

    model_id = "stub-hash-v1"

    def __init__(self, *, dim: int | None = None) -> None:
        # `dim` se lee de `settings` en cada acceso (no fijado al construir):
        # el objeto se cachea por nombre de proveedor (`get_provider`), y algún
        # test podría monkeypatchear `settings.embedding_dim` a futuro.
        self._fixed_dim = dim

    @property
    def dim(self) -> int:
        return self._fixed_dim if self._fixed_dim is not None else settings.embedding_dim

    def embed_query(self, text: str) -> list[float]:
        return _stub_embed(text, self.dim)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [_stub_embed(t, self.dim) for t in texts]


class LocalSentenceTransformerProvider(EmbeddingProvider):
    """`sentence-transformers` en el propio proceso — proveedor LOCAL/soberano.

    Carga LAZY: el modelo (~400MB) solo se descarga/carga la PRIMERA vez que se
    accede a `.dim`/`embed_query`/`embed_documents` — nunca al importar este
    módulo ni al construir el objeto. `app/main.py::lifespan` fuerza esa
    primera carga al arrancar (para poder validar la dimensión fail-closed),
    así que en producción el costo se paga UNA vez al boot del contenedor, no
    por request.
    """

    def __init__(self, *, model_id: str) -> None:
        self.model_id = model_id
        # `Any` y no `None`: el modelo se carga perezosamente en
        # `_ensure_loaded`. Sin anotación, el atributo queda inferido como
        # `None` y la asignación posterior del `SentenceTransformer` real solo
        # pasa de refilón, porque la dependencia es opcional y mypy ya la ve
        # como `Any`. Anotarlo lo dice explícitamente.
        self._model: Any = None

    def _ensure_loaded(self):  # noqa: ANN202 — tipo del modelo es de una dep opcional
        if self._model is None:
            from sentence_transformers import SentenceTransformer  # import perezoso

            self._model = SentenceTransformer(self.model_id)
        return self._model

    @property
    def dim(self) -> int:
        model = self._ensure_loaded()
        return int(model.get_sentence_embedding_dimension())

    def embed_query(self, text: str) -> list[float]:
        model = self._ensure_loaded()
        vec = model.encode(text, normalize_embeddings=True)
        return vec.tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        model = self._ensure_loaded()
        vecs = model.encode(texts, normalize_embeddings=True)
        return vecs.tolist()


class OllamaEmbeddingProvider(EmbeddingProvider):
    """MÍNIMO VIABLE: HTTP directo a `{base_url}/api/embeddings` (Ollama).

    NO se cachea `.dim` de forma perezosa-perenne entre instancias distintas —
    cada instancia (una por nombre de proveedor, ver `get_provider`) memoiza su
    propia primera respuesta. Deliberadamente DIFERIDO más allá de esto (sin
    reintentos/backoff/streaming): no hay contenedor Ollama en
    `docker-compose.yml` de este repo; queda listo para un despliegue externo.
    """

    def __init__(self, *, model_id: str, base_url: str) -> None:
        self.model_id = model_id
        self._base_url = base_url.rstrip("/")
        self._dim: int | None = None

    def _request(self, text: str) -> list[float]:
        import httpx

        resp = httpx.post(
            f"{self._base_url}/api/embeddings",
            json={"model": self.model_id, "prompt": text},
            timeout=30.0,
        )
        resp.raise_for_status()
        return list(resp.json()["embedding"])

    @property
    def dim(self) -> int:
        if self._dim is None:
            self._dim = len(self._request("dimension probe"))
        return self._dim

    def embed_query(self, text: str) -> list[float]:
        return self._request(text)

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._request(t) for t in texts]


_provider_cache: dict[str, EmbeddingProvider] = {}

# Únicos nombres con una IMPLEMENTACIÓN REAL registrada (más allá del stub). Un
# `EMBEDDING_PROVIDER` que no sea ninguno de estos tres es un error de
# configuración — `get_provider()`/`_build_provider` lo rechazan explícito
# (usado por el guard de arranque de `app/main.py` y por `app/ops/reembed.py`).
_REAL_PROVIDER_NAMES = frozenset({"stub", "local_st", "ollama"})


def _build_provider(name: str) -> EmbeddingProvider:
    if name == "stub":
        return StubEmbeddingProvider()
    if name == "local_st":
        return LocalSentenceTransformerProvider(model_id=settings.model_embed)
    if name == "ollama":
        return OllamaEmbeddingProvider(model_id=settings.model_embed, base_url=settings.ollama_url)
    raise ValueError(
        f"embedding_provider={name!r} desconocido (esperaba 'stub', 'local_st' u 'ollama')"
    )


def get_provider(name: str | None = None) -> EmbeddingProvider:
    """Devuelve la instancia CACHEADA del proveedor (una por nombre, por proceso).

    La caché es deliberada: `LocalSentenceTransformerProvider`/
    `OllamaEmbeddingProvider` memoizan su propia carga lazy en la instancia, así
    que reconstruir el objeto en cada llamada anularía el cacheo del modelo.

    ESTRICTO a propósito (un nombre desconocido levanta `ValueError`) — es el
    punto que usan `app/main.py::lifespan` y `app/ops/reembed.py` para fallar
    RÁPIDO ante un `EMBEDDING_PROVIDER` mal escrito, antes de tocar ninguna
    fila. `embed()` (abajo) NO pasa por aquí para nombres no reconocidos —
    ver su docstring.
    """
    resolved = name or settings.embedding_provider
    if resolved not in _provider_cache:
        _provider_cache[resolved] = _build_provider(resolved)
    return _provider_cache[resolved]


def embed(text: str, dim: int | None = None) -> list[float]:
    """Punto de entrada histórico. Con `dim` explícito: SIEMPRE el algoritmo
    stub determinista a esa dimensión (uso: tests unitarios del propio stub,
    herramientas ad-hoc). Sin `dim`: despacha al proveedor REAL configurado
    (`local_st`/`ollama`) vía `get_provider()`.

    Para `"stub"` o cualquier OTRO nombre no reconocido en
    `_REAL_PROVIDER_NAMES`, calcula el hash determinista directamente (SIN
    pasar por `get_provider()`/`_build_provider`, que rechazarían un nombre
    desconocido). Esto preserva el comportamiento HISTÓRICO de `embed()`: el
    algoritmo en sí nunca dependió de `EMBEDDING_PROVIDER` — solo
    `is_embeddable`/`is_local_embedding_provider` (el GATE de residencia) leen
    ese nombre como política. Es deliberado para no romper el patrón de test
    establecido (`monkeypatch.setattr(settings, "embedding_provider",
    "openai")`/`"external"` como STAND-IN de "algún proveedor externo" para
    ejercer el gate, sin necesitar una implementación real registrada). En
    producción esta rama es INALCANZABLE con una config inválida: el guard de
    arranque de `app/main.py` llama a `get_provider()` DIRECTAMENTE (estricto,
    sin este fallback) y aborta antes de aceptar tráfico si el nombre no es
    real — solo un test que nunca ejecuta el lifespan llega aquí con un
    nombre no reconocido.
    """
    if dim is not None:
        return _stub_embed(text, dim)
    # Despacho derivado de la FUENTE ÚNICA `_REAL_PROVIDER_NAMES` (menos `stub`,
    # que se computa inline aquí): un proveedor real que se registre a futuro en
    # `_build_provider` + `_REAL_PROVIDER_NAMES` se enruta por `get_provider()`
    # automáticamente, sin caer en silencio al stub (remediación auditoría Baja).
    if settings.embedding_provider in (_REAL_PROVIDER_NAMES - {"stub"}):
        return get_provider().embed_query(text)
    return _stub_embed(text, settings.embedding_dim)
