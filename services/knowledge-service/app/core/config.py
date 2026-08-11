# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+asyncpg://orpycamcp:orpycamcp@postgres:5432/orpycamcp_db"

    # ── Presupuesto de conexiones a Postgres ──────────────────────────────
    # Configurables porque el reparto NO es una constante del código: depende
    # de cuántos servicios y réplicas haya en el despliegue. La aritmética
    # completa está escrita en `infra/docker-compose.prod.yml`, junto al
    # `max_connections` del servidor, que es donde tiene sentido cuadrarla.
    db_pool_min_size: int = 2
    db_pool_max_size: int = 10
    # Tope por CONSULTA. Sin él, una consulta atascada retiene su conexión
    # indefinidamente y el pool se agota sin que nada lo delate. 120 s es muy
    # superior a cualquier consulta sana de este sistema; lo que legítimamente
    # tarde más debe pasar su propio `timeout=` en la llamada (es lo que hace
    # `orpycamcp_common.audit.verify_chain`, que recorre el registro entero).
    db_command_timeout: float = 120.0
    redis_url: str = "redis://redis:6379/0"

    @property
    def database_url_asyncpg(self) -> str:
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://")

    # E21: grupo de consumidores PROPIO sobre orpycamcp.document.events. Distinto
    # del grupo de workflow-service (auto-distribución) — Redis Streams entrega una
    # copia independiente del stream a cada consumer-group, así que ambos servicios
    # consumen el mismo stream sin competir entre sí.
    knowledge_ingest_group: str = "knowledge-ingest"

    # Proveedor de embeddings (ADR-006/E21 Inc.1: pluggable). Default REAL de
    # despliegue: `local_st` (sentence-transformers en el propio contenedor —
    # soberano, no envía texto a ningún tercero). `stub` (determinista, sin
    # modelo) queda como fallback de test/dev — los tests FUERZAN "stub" a nivel
    # de sesión en `tests/conftest.py` para no descargar el modelo nunca en CI.
    # `ollama` es un proveedor MÍNIMO VIABLE (ver `app/services/embeddings.py`;
    # requiere un Ollama accesible en `ollama_url`, no provisionado por defecto
    # en `docker-compose.yml` de este repo).
    embedding_provider: str = "local_st"
    # EMBEDDING_DIM es la fuente ÚNICA de la dimensión de `knowledge_chunk.embedding`
    # (una sola columna/índice ANN por schema, migración 007). El provider
    # seleccionado DEBE producir vectores de esta dimensión o el arranque aborta
    # (guard fail-closed en `app/main.py::lifespan`).
    embedding_dim: int = 384
    # Modelo del proveedor real. Para `local_st`: nombre de modelo de
    # sentence-transformers (multilingüe, 384 dim). Para `ollama`: el tag del
    # modelo servido por esa instancia (el operador debe sobreescribir este
    # valor si usa `ollama`, el default está pensado para `local_st`).
    model_embed: str = "paraphrase-multilingual-MiniLM-L12-v2"
    # Solo relevante si embedding_provider="ollama" (proveedor mínimo viable).
    ollama_url: str = "http://ollama:11434"
    service_port: int = 8011
    log_level: str = "info"

    # Token de ingesta (D-02 / RF-SEG-08): cadena compartida que deben enviar los
    # servicios internos en X-Internal-Token para invocar /ingest. Si está vacío el
    # endpoint rechaza TODAS las llamadas (fail-closed).  Nunca exponer en logs.
    internal_service_token: str = ""

    # --- E21 Inc.3: RAG generativo con citas (Patrón B) --------------------------
    # Selección de proveedor de GENERACIÓN (distinto de EMBEDDING_PROVIDER, que
    # solo cubre embeddings). `disabled` (o cualquier nombre no registrado) NO
    # genera nada — el router cae al Patrón A (recuperación pura, `/antecedentes`).
    # `ollama_local` es el DEFAULT soberano (mismo contenedor Ollama que
    # EMBEDDING_PROVIDER=ollama podría usar, ver `ollama_url`). `ollama_cloud` y
    # `openai_compatible` son proveedores EXTERNOS mínimo-viables (mismo shape de
    # API, endpoint distinto). `anthropic` es opción explícita (NO default) — usa
    # el SDK oficial con la Citations API nativa (anclaje fuerte).
    #
    # Barrera dura de soberanía (decisión TOMADA, ver documentos/specDrive/
    # 00-capa-conocimiento.md y app/core/clearance.py::is_generatable): NO existe
    # un `ALLOW_CLASSIFIED_EXTERNAL` — material nivel>=RESERVADA NUNCA se envía a
    # un proveedor externo, sin excepción configurable.
    ai_provider: str = "ollama_local"
    # Vacío = cada proveedor resuelve SU PROPIO default sensato (ver
    # `app/services/llm.py::_build_llm_provider`) — así no hay que fijar aquí un
    # modelo que solo tiene sentido para un proveedor concreto.
    model_generation: str = ""
    # Solo si AI_PROVIDER=anthropic (opt-in explícito). Vacío → el provider aborta
    # con RAGUnavailableError (degradación HONESTA, RF-FIR-15: nunca simula una
    # respuesta) y el router responde 503 `rag_unavailable`.
    anthropic_api_key: str = ""
    # AI_PROVIDER=ollama_local reusa `ollama_url` (arriba, ya existe para
    # EMBEDDING_PROVIDER=ollama) — es la MISMA instancia local/soberana.
    ollama_cloud_url: str = "https://ollama.com"
    ollama_cloud_api_key: str = ""
    openai_compatible_url: str = ""
    openai_compatible_api_key: str = ""


settings = Settings()
