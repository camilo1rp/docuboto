# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core import config as cfg_module
from app.core.database import get_tenant_conn
from app.main import app

# E21 Inc.1: el default de DESPLIEGUE de `embedding_provider` es `local_st`
# (sentence-transformers real, ver app/core/config.py) — pero la suite entera
# (unit + integration, este conftest.py es el padre de tests/integration/)
# jamás debe descargar/cargar ese modelo. Se fuerza `stub` a nivel de MÓDULO
# (una sola vez, al importar este conftest — antes de que corra cualquier
# test), no con un fixture: así el valor por defecto que ve cada test es
# "stub" sin que cada archivo tenga que pedirlo explícitamente, y los
# `monkeypatch.setattr(cfg_module.settings, "embedding_provider", ...)`
# puntuales (simulando un proveedor externo) lo restauran a "stub" —no al
# default de clase `local_st`— al terminar cada test.
cfg_module.settings.embedding_provider = "stub"

# E21 Inc.3: gemelo de lo anterior para GENERACIÓN — el default de despliegue de
# `ai_provider` (`ollama_local`) apuntaría a `http://ollama:11434` (sin
# infraestructura Ollama en este repo). Se fuerza `"disabled"` a nivel de MÓDULO
# para que NINGÚN test dispare accidentalmente un intento de red real ni cargue el
# SDK `anthropic` — `POST /rag` con `AI_PROVIDER=disabled` cae siempre al Patrón A
# (recuperación pura, sin invocar ningún `LLMProvider`). Tests que SÍ quieran
# ejercer la rama generativa deben mockear `app.services.llm.get_llm_provider` (o
# monkeypatchear `ai_provider` a un nombre real Y mockear el provider) — nunca
# dejar que la llamada real llegue a `httpx`/al SDK.
cfg_module.settings.ai_provider = "disabled"

TENANT_HEADERS = {"X-Tenant-Slug": "demo"}

# Token compartido que usan los tests que ejercen el endpoint /ingest.
TEST_INTERNAL_TOKEN = "test-internal-service-token-xxx"


@pytest.fixture
def token_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Activa el token de ingesta para tests que necesitan llamar a /ingest."""
    monkeypatch.setattr(cfg_module.settings, "internal_service_token", TEST_INTERNAL_TOKEN)


@pytest_asyncio.fixture
async def client_with_conn() -> AsyncGenerator[tuple[AsyncClient, AsyncMock], None]:
    conn = AsyncMock()
    conn.execute.return_value = None
    conn.fetchrow.return_value = None
    conn.fetch.return_value = []

    # `conn.transaction()` de asyncpg es un context manager async (no un
    # coroutine). El default de AsyncMock lo haría awaitable y rompería
    # `async with conn.transaction():` en el router (E21 Inc.3, escritura de
    # kb_suggestions + asiento en la misma tx). Lo sustituimos por una factory
    # que devuelve un CM async no-op reentrante.
    @asynccontextmanager
    async def _fake_tx() -> AsyncGenerator[None, None]:
        yield

    conn.transaction = MagicMock(side_effect=lambda *a, **k: _fake_tx())

    async def _dep() -> AsyncGenerator[AsyncMock, None]:
        yield conn

    app.dependency_overrides[get_tenant_conn] = _dep
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, conn
    app.dependency_overrides.clear()
