# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from orpycamcp_common import events, identidad, request_log

from app.core.clearance import url_es_local
from app.core.config import settings
from app.core.database import close_pool, create_pool
from app.core.redis_client import close_redis, init_redis
from app.routers.admin_deadletter import router as admin_deadletter_router
from app.routers.health import router as health_router
from app.routers.knowledge import router as knowledge_router
from app.services.embeddings import get_provider
from app.workers import document_consumer

logging.basicConfig(level=settings.log_level.upper())
logger = logging.getLogger(__name__)


def _assert_embedding_dim_matches_provider() -> None:
    """Guard FAIL-CLOSED de arranque (E21 Inc.1): `knowledge_chunk.embedding` es
    UNA columna `vector(EMBEDDING_DIM)` por schema (migración 007) — si el
    proveedor configurado (`EMBEDDING_PROVIDER`) produce una dimensión distinta,
    cada INSERT/UPSERT fallaría en tiempo de request (o peor, silenciosamente
    corrompería el índice ANN si pgvector permitiera la mezcla). Se verifica UNA
    vez al arrancar, no por-request: aborta el proceso antes de aceptar tráfico
    en vez de fallar tarde y de forma dispersa.

    Para `local_st`/`ollama` esto FUERZA la carga (lazy) del modelo/la primera
    llamada aquí mismo — es el costo de arranque aceptado por diseño (ver
    docstring de `LocalSentenceTransformerProvider`); nunca ocurre en tests
    (la suite fuerza `EMBEDDING_PROVIDER=stub` y nunca ejecuta este lifespan,
    ver `tests/conftest.py`).
    """
    provider = get_provider()
    if provider.dim != settings.embedding_dim:
        raise RuntimeError(
            f"embedding_provider={settings.embedding_provider!r} (modelo "
            f"{provider.model_id!r}) produce vectores de dimensión {provider.dim}, "
            f"pero EMBEDDING_DIM={settings.embedding_dim} (columna "
            "knowledge_chunk.embedding, migración 007). Arranque abortado "
            "(fail-closed) — ajusta EMBEDDING_DIM o el proveedor."
        )
    logger.info(
        "embedding provider=%s modelo=%s dim=%d (OK, coincide con EMBEDDING_DIM)",
        settings.embedding_provider,
        provider.model_id,
        provider.dim,
    )


def _warn_if_ollama_url_not_local() -> None:
    """Aviso de arranque cuando `OLLAMA_URL` no es local.

    La residencia de `ollama_local` ya NO depende de este aviso: `is_generatable`
    exige que el host de `OLLAMA_URL` sea local (`url_es_local`) y, si no lo es,
    excluye del contexto del RAG todo chunk de nivel >= RESERVADA (AREA-11-12,
    fail-closed). El warning queda para que el operador entienda por qué el RAG
    cae a Patrón A con material reservado. Para `EMBEDDING_PROVIDER=ollama` la
    ingesta de nivel >= 2 ya estaba bloqueada por nombre (`is_embeddable`)."""
    if settings.ai_provider != "ollama_local" and settings.embedding_provider != "ollama":
        return
    if url_es_local(settings.ollama_url):
        return
    logger.warning(
        "OLLAMA_URL=%r no es un endpoint LOCAL/privado: el material nivel >= "
        "RESERVADA NO se enviará a ese host (el RAG lo excluye del contexto y cae "
        "a Patrón A). Para usarlo con material reservado, apunta OLLAMA_URL a un "
        "host loopback, de la red privada, de contenedor o *.local operado por el "
        "propio despliegue (E21, barrera dura de soberanía).",
        settings.ollama_url,
    )


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("knowledge-service starting up")
    _assert_embedding_dim_matches_provider()
    _warn_if_ollama_url_not_local()
    app.state.db_pool = await create_pool(settings.database_url_asyncpg)
    await init_redis(settings.redis_url)
    # Ingesta event-driven (E21): auto-puebla el índice pgvector consumiendo
    # orpycamcp.document.events (patrón exacto de workflow-service main.py).
    consumer_task = events.spawn_supervised(
        document_consumer.run, name="knowledge", logger=logger
    )
    yield
    logger.info("knowledge-service shutting down")
    consumer_task.cancel()
    try:
        await consumer_task
    except asyncio.CancelledError:
        pass
    await close_pool()
    await close_redis()


app = FastAPI(
    title="OrpycaMCP — Knowledge Service",
    description=(
        "Capa de conocimiento ADVISORIA y derivada (E21, ADR-006): recuperación "
        "semántica (pgvector) con pre-filtrado ACL. La ingesta y el proveedor de IA son "
        "GLOBALES de la instalación (no hay opt-in por entidad, OMI-04); no es fuente "
        "de verdad. Embeddings con proveedor pluggable (local_st por defecto)."
    ),
    version="1.0.0",
    lifespan=lifespan,
)

# Registro estructurado por petición (11.2). Va aquí, con el `X-Request-Id` que
# el gateway propaga desde el borde: sin esto el identificador llega y nadie lo
# escribe, que es el mismo defecto de partida movido un salto más allá.
# La identidad que inyecta el gateway viene FIRMADA: sin esto, cualquiera
# que alcance el puerto de este servicio dentro de la red podía hablarle
# diciendo ser quien quisiera y de la entidad que quisiera. Va el primero
# —o sea el más externo— porque es la decisión que precede a todas.
app.add_middleware(identidad.IdentidadFirmadaMiddleware)
app.add_middleware(request_log.RequestLogMiddleware, servicio="knowledge-service")

app.include_router(knowledge_router)
app.include_router(admin_deadletter_router)
app.include_router(health_router)
