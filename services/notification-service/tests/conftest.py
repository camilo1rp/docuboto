# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Fixtures de notification-service.

La BD (pool + get_tenant_conn + acquire_tenant) y Redis se mockean para correr
sin infraestructura. El historial se persiste/lee en la BD del tenant (E16).
"""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.config import settings

# Las pruebas unitarias llaman a la aplicación DIRECTAMENTE, sin gateway.
#
# Desde que la identidad va firmada, eso es exactamente lo que el servicio
# rechaza en producción: una petición con cabeceras de usuario que nadie firmó.
# Aquí se corre en el modo «firma no activada», que es una configuración
# legítima —la que tiene un despliegue que aún no puso el secreto— y no un
# atajo: la firma en sí la cubren `shared/tests/test_la_identidad_viene_firmada.py`
# y la comprobación contra la pila levantada.
os.environ.pop("IDENTITY_SIGNING_SECRET", None)


# X-User-Id carries the internal auth_users.id UUID (not keycloak_sub).
# auth-service /validate resolves keycloak_sub → auth_users.id before the gateway
# injects the header; tests use a fixed UUID that matches the mock's user row.
USER = uuid.UUID("11111111-1111-1111-1111-111111111111")

# Token interno de pruebas para `POST /send` (sellado servicio-a-servicio, D-02).
_TEST_INTERNAL_TOKEN = "test-internal-token"
INTERNAL_HEADERS = {"X-Internal-Token": _TEST_INTERNAL_TOKEN}


@pytest.fixture(autouse=True)
def _configure_internal_token() -> AsyncGenerator[None, None]:
    """Deja `INTERNAL_SERVICE_TOKEN` configurado en TODAS las pruebas.

    Sin esto el gate de `/send` está fail-closed (401 `internal_access_not_configured`)
    y no se podría distinguir "token ausente" de "servicio sin configurar". Con el
    secreto fijado, un envío legítimo debe portar `INTERNAL_HEADERS`.
    """
    previo = settings.internal_service_token
    settings.internal_service_token = _TEST_INTERNAL_TOKEN
    yield
    settings.internal_service_token = previo


#: KEK de pruebas del secreto de los webhooks (base64 de 32 bytes). La v2 sirve a
#: las pruebas de rotación.
TEST_WEBHOOK_KEK_V1 = "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE="
TEST_WEBHOOK_KEK_V2 = "AgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgI="


@pytest.fixture(autouse=True)
def _configure_webhook_kek() -> AsyncGenerator[None, None]:
    """Deja el llavero de los secretos de webhook configurado en TODAS las pruebas
    (crear un webhook con secreto cifra y, sin KEK, falla cerrado)."""
    previos = (
        settings.webhook_secret_kek,
        settings.webhook_secret_keks,
        settings.webhook_secret_kek_active_version,
    )
    settings.webhook_secret_kek = TEST_WEBHOOK_KEK_V1
    settings.webhook_secret_keks = {}
    settings.webhook_secret_kek_active_version = 1
    yield
    (
        settings.webhook_secret_kek,
        settings.webhook_secret_keks,
        settings.webhook_secret_kek_active_version,
    ) = previos


def make_redis_mock() -> MagicMock:
    mock = MagicMock()
    mock.ping = AsyncMock()
    mock.xgroup_create = AsyncMock()
    mock.xreadgroup = AsyncMock(return_value=None)
    mock.xack = AsyncMock()
    mock.aclose = AsyncMock()
    return mock


def make_db_conn() -> AsyncMock:
    conn = AsyncMock()
    conn.execute.return_value = None
    conn.fetchrow.return_value = None
    conn.fetch.return_value = []
    # check_permission (require_permission dep, ahora en /history · /{id} y en
    # el router de webhooks) usa fetchval: True = usuario autorizado por defecto.
    # Los tests que quieren 403 lo fuerzan a None.
    conn.fetchval.return_value = True
    # La traza best-effort de denegación (`audit.append_denial`) abre
    # `async with conn.transaction():`; sin esto el mock devuelve una corrutina
    # sin await (RuntimeWarning). Mismo tratamiento que `_make_mock_conn`.
    conn.transaction = MagicMock(return_value=AsyncMock())
    return conn


class _FakeAcquire:
    """Sustituye a acquire_tenant(slug): async context que cede el mock conn."""

    def __init__(self, conn: AsyncMock) -> None:
        self._conn = conn

    async def __aenter__(self) -> AsyncMock:
        return self._conn

    async def __aexit__(self, *args: object) -> bool:
        return False


@pytest.fixture
def redis_mock() -> MagicMock:
    return make_redis_mock()


@pytest.fixture
def db_conn() -> AsyncMock:
    return make_db_conn()


@pytest.fixture
def app_with_mocks(redis_mock: MagicMock, db_conn: AsyncMock):
    from app.core.database import get_tenant_conn

    async def _tenant_conn() -> AsyncGenerator[AsyncMock, None]:
        yield db_conn

    with (
        patch("app.main.init_pool", new=AsyncMock()),
        patch("app.main.close_pool", new=AsyncMock()),
        patch("app.main.init_redis", new=AsyncMock(return_value=redis_mock)),
        patch("app.main.close_redis", new=AsyncMock()),
        patch(
            "app.main.stream_worker.run",
            new=AsyncMock(side_effect=asyncio.CancelledError),
        ),
        patch("app.core.redis_client.get_redis", return_value=redis_mock),
        patch("app.routers.health.get_redis", return_value=redis_mock),
        patch(
            "app.services.notification_service.acquire_tenant",
            return_value=_FakeAcquire(db_conn),
        ),
    ):
        from app.main import app

        app.dependency_overrides[get_tenant_conn] = _tenant_conn
        yield app
        app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def client(app_with_mocks) -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(
        transport=ASGITransport(app=app_with_mocks), base_url="http://test"
    ) as ac:
        yield ac


def make_db_row(
    *,
    nid: uuid.UUID | None = None,
    recipient_email: str = "user@example.com",
    subject: str = "Subject",
    body: str = "Body",
    status: str = "sent",
    event_type: str | None = None,
    sent_at: datetime | None = None,
    attempts: int = 1,
    last_error: str | None = None,
    sensible: bool = False,
) -> dict:
    return {
        "id": nid or uuid.uuid4(),
        "recipient_email": recipient_email,
        "subject": subject,
        "body": body,
        "status": status,
        "event_type": event_type,
        "sent_at": sent_at or datetime.now(tz=UTC),
        "attempts": attempts,
        "last_error": last_error,
        "sensible": sensible,
    }


TENANT_HEADERS = {"X-Tenant-Slug": "icetex", "X-User-Id": str(USER)}


def _make_mock_conn() -> AsyncMock:
    """Mock asyncpg connection for admin DLQ router tests (ADR-021 Fase 5 Inc.2)."""
    conn = AsyncMock()
    conn.execute.return_value = None
    conn.fetchrow.return_value = None
    # Default True: check_permission (require_permission dep) sees the user as authorised.
    # Tests that need permission denied override with fetchval.return_value = None.
    conn.fetchval.return_value = True
    # audit.append uses `async with conn.transaction():` then conn.execute (INSERT);
    # replay/discard also use `async with conn.transaction():` around the whole handler.
    conn.transaction = MagicMock(return_value=AsyncMock())
    return conn


@pytest_asyncio.fixture
async def client_with_conn() -> AsyncGenerator[tuple[AsyncClient, AsyncMock], None]:
    """Yields (client, mock_conn) with PERM_DLQ_ADMIN granted and tenant conn overridden.

    Mirrors signature-service's fixture of the same name. Used by the admin DLQ
    router tests, which need direct control over conn.fetchrow/fetch/execute
    without the notification/webhook-specific patches in app_with_mocks.
    """
    from app.core.database import get_tenant_conn
    from app.main import app

    conn = _make_mock_conn()

    async def _conn_dep() -> AsyncGenerator[AsyncMock, None]:
        yield conn

    app.dependency_overrides[get_tenant_conn] = _conn_dep
    # La resolución del buzón de la dependencia se aísla a propósito: estos
    # tests controlan `conn.fetchrow` directamente para la fila de la cola, y
    # la resolución usa esa MISMA conexión. Mezclarlas haría que cada test
    # tuviera que devolver dos cosas por el mismo mock. El comportamiento del
    # buzón tiene sus propios tests (`test_buzones.py`), y que el reenvío
    # RESUELVA —en vez de inventar la dirección— lo comprueba
    # `test_replay_workflow_resuelve_el_buzon_de_la_dependencia`.
    with patch(
        "app.routers.admin_deadletter.resolver_buzon_dependencia",
        AsyncMock(return_value="dependencia-902@orpycamcp.local"),
    ):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac, conn
    app.dependency_overrides.clear()
