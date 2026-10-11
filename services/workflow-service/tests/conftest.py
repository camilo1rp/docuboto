# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Test fixtures for workflow-service.

DB dependency (get_db_conn) and Redis (get_redis) are both mocked so tests
run without real infrastructure.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.database import get_db_conn
from app.main import app
from app.repositories.workflow_repository import Tenencia

# Las pruebas unitarias llaman a la aplicación DIRECTAMENTE, sin gateway.
#
# Desde que la identidad va firmada, eso es exactamente lo que el servicio
# rechaza en producción: una petición con cabeceras de usuario que nadie firmó.
# Aquí se corre en el modo «firma no activada», que es una configuración
# legítima —la que tiene un despliegue que aún no puso el secreto— y no un
# atajo: la firma en sí la cubren `shared/tests/test_la_identidad_viene_firmada.py`
# y la comprobación contra la pila levantada.
os.environ.pop("IDENTITY_SIGNING_SECRET", None)


TENANT_SLUG = "demo"
USER_ID = uuid.uuid4()
TENANT_HEADERS = {
    "X-Tenant-Slug": TENANT_SLUG,
    "X-User-Id": str(USER_ID),
}

RADICADO_ID = uuid.uuid4()
STEP_ID = uuid.uuid4()
TRACKING_NUMBER = "2024-DEMO-E-000001"
NOW = datetime.now(tz=UTC)


def make_step_record(
    *,
    step_id: uuid.UUID | None = None,
    radicado_id: uuid.UUID | None = None,
    tracking_number: str = TRACKING_NUMBER,
    step_number: int = 1,
    from_dept: str | None = None,
    to_dept: str = "Dirección Jurídica",
    to_dept_code: int | None = 4,
    from_dept_code: int | None = None,
    assigned_to: uuid.UUID | None = None,
    assigned_by: uuid.UUID | None = None,
    action: str = "assign",
    notes: str | None = None,
    step_status: str = "pending",
    created_at: datetime | None = None,
    completed_at: datetime | None = None,
    doc_type: str | None = "E",
    due_at: datetime | None = None,
    overdue_notified: bool = False,
) -> dict:
    return {
        "id": step_id or STEP_ID,
        "radicado_id": radicado_id or RADICADO_ID,
        "tracking_number": tracking_number,
        "step_number": step_number,
        "from_dept": from_dept,
        "to_dept": to_dept,
        "from_dept_code": from_dept_code,
        "to_dept_code": to_dept_code,
        "assigned_to": assigned_to or uuid.uuid4(),
        "assigned_by": assigned_by or USER_ID,
        "action": action,
        "notes": notes,
        "status": step_status,
        "created_at": created_at or NOW,
        "completed_at": completed_at,
        "doc_type": doc_type,
        "due_at": due_at,
        "overdue_notified": overdue_notified,
    }


def make_event_record(
    *,
    event_id: uuid.UUID | None = None,
    radicado_id: uuid.UUID | None = None,
    tracking_number: str = TRACKING_NUMBER,
    tipo_tx: str = "assign",
    from_dept: str | None = None,
    to_dept: str | None = "Dirección Jurídica",
    to_dept_code: int | None = 4,
    from_dept_code: int | None = None,
    actor: uuid.UUID | None = None,
    comentario: str | None = None,
    detalles: dict | None = None,
    ts: datetime | None = None,
) -> dict:
    return {
        "id": event_id or uuid.uuid4(),
        "radicado_id": radicado_id or RADICADO_ID,
        "tracking_number": tracking_number,
        "tipo_tx": tipo_tx,
        "from_dept": from_dept,
        "to_dept": to_dept,
        "from_dept_code": from_dept_code,
        "to_dept_code": to_dept_code,
        "actor": actor or USER_ID,
        "comentario": comentario,
        "detalles": detalles if detalles is not None else {},
        "ts": ts or NOW,
    }


def _make_mock_conn() -> AsyncMock:
    conn = AsyncMock()
    conn.fetchval.return_value = 1
    conn.execute.return_value = None
    conn.fetch.return_value = []
    conn.fetchrow.return_value = None
    # `async with conn.transaction()` (escritura atómica paso + flow_event).
    conn.transaction = MagicMock(return_value=AsyncMock())
    return conn


def _make_mock_redis() -> MagicMock:
    redis = MagicMock()
    redis.xadd = AsyncMock(return_value="1-0")
    return redis


@pytest.fixture(autouse=True)
def _sin_sincronizacion_de_estado() -> Generator[AsyncMock, None, None]:
    """ADR-028: por defecto la sincronización del estado del radicado es un no-op.

    Los tests unitarios de este directorio dirigen una conexión MOCK con
    secuencias de `fetchrow`/`fetchval` pensadas para el acto que prueban; la
    consulta real de `sincronizar_estado_radicado` (`radicados` + `flow_steps`)
    las desordenaría sin aportar señal. Su lógica SQL y su atomicidad las prueban
    los tests de `tests/integration/` contra Postgres real; aquí se prueba el
    CABLEADO (qué actos la llaman y qué hacen con su respuesta) sobrescribiendo
    el valor de retorno del mock que este fixture entrega.

    Por la misma razón se neutralizan las tres lecturas que añadió la remediación
    de auditoría a `assign`/`transfer`/`finalizar` (clearance del actor, tenencia
    del paso y contexto del cierre): por defecto el actor alcanza todo, no hay
    paso activo ajeno y el contexto «no consta». Los tests que prueban ESA lógica
    sobrescriben estos mocks (`app.services.workflow_service.WorkflowRepository.*`).
    """
    repo = "app.services.workflow_service.WorkflowRepository"
    with (
        patch(f"{repo}.sincronizar_estado_radicado", new=AsyncMock(return_value=None)) as sync,
        patch(f"{repo}.clearance_para_acto", new=AsyncMock(return_value=3)),
        patch(f"{repo}.tenencia", new=AsyncMock(return_value=Tenencia(False, False))),
        patch(f"{repo}.puede_repartir_sin_paso_activo", new=AsyncMock(return_value=True)),
        # ADR-032: el origen de un paso se deriva del paso activo; por defecto no hay.
        patch(f"{repo}.paso_activo", new=AsyncMock(return_value=None)),
        patch(
            f"{repo}.contexto_cierre",
            new=AsyncMock(
                return_value={
                    "tiene_respuesta": None,
                    "en_expediente": None,
                    "radicado_vencido": None,
                }
            ),
        ),
    ):
        yield sync


@pytest_asyncio.fixture(scope="function")
async def client_with_conn() -> AsyncGenerator[tuple[AsyncClient, AsyncMock, MagicMock], None]:
    """Yields (client, mock_conn, mock_redis) so tests can configure mocks per-call."""
    mock_conn = _make_mock_conn()
    mock_redis = _make_mock_redis()

    async def override_get_db_conn() -> AsyncGenerator[AsyncMock, None]:
        yield mock_conn

    app.dependency_overrides[get_db_conn] = override_get_db_conn

    with patch("app.services.workflow_service.get_redis", return_value=mock_redis):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac, mock_conn, mock_redis

    app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def client() -> AsyncGenerator[AsyncClient, None]:
    """Simple client — use when you don't need to inspect mock internals."""
    mock_conn = _make_mock_conn()
    mock_redis = _make_mock_redis()

    async def override_get_db_conn() -> AsyncGenerator[AsyncMock, None]:
        yield mock_conn

    app.dependency_overrides[get_db_conn] = override_get_db_conn

    with patch("app.services.workflow_service.get_redis", return_value=mock_redis):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac

    app.dependency_overrides.clear()
