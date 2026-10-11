# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Test fixtures for document-service.

The tenant DB dependency (get_tenant_conn) and the global one (get_db_conn,
used by /health) are overridden with an AsyncMock connection so tests run
without a real PostgreSQL instance. The real get_tenant_conn validates the
X-Tenant-Slug header and sets search_path; tests that exercise that validation
pop the override explicitly.
"""

import os
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.clients.tenant_client import TenantServiceError
from app.core.database import (
    get_db_conn,
    get_redis,
    get_tenant_client,
    get_tenant_conn,
)
from app.main import app

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
TENANT_CODE = "DEMO"
TENANT_HEADERS = {
    "X-Tenant-Slug": TENANT_SLUG,
    "X-User-Id": str(uuid.uuid4()),
}


class FakeTenantClient:
    """Sustituye al cliente HTTP a tenant-service; registra llamadas."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, date, int]] = []
        self.due_date: date = date(2024, 1, 22)
        self.raise_error: bool = False
        # Organigrama simulado: codigo -> dependencia.
        # La 1 existe por defecto: Ac. AGN 060/2001 §5.2 (AREA-09-02) hace obligatoria
        # la dependencia al radicar, y casi todo payload de prueba lleva `dest_dept_code=1`.
        self.dependencias: dict[int, dict] = {
            1: {"codigo": 1, "sigla": "DG", "nombre": "Dirección General", "activo": True}
        }
        self.dep_calls: list[tuple[str, int]] = []
        self.dep_raise_error: bool = False
        # Parámetros de la entidad (`sgd_param_admin`), que alimentan el
        # membrete del documento de salida (E23, RF-PRO-04).
        self.params: dict[str, str] = {}
        self.params_error: bool = False
        self.estricto_calls: int = 0

    async def calculate_due_date(
        self, *, tenant_slug: str, from_date: date, business_days: int
    ) -> date:
        self.calls.append((tenant_slug, from_date, business_days))
        if self.raise_error:
            raise TenantServiceError("simulated tenant-service failure")
        return self.due_date

    async def get_params(self, *, tenant_slug: str) -> dict:
        return self.params

    async def get_params_estricto(self, *, tenant_slug: str) -> dict:
        """Lectura estricta (política de tratamiento, B2). Por defecto la entidad
        TIENE política vigente `2026-01`; `params` la pisa y `params_error` la rompe."""
        self.estricto_calls += 1
        if self.params_error:
            raise TenantServiceError("simulated tenant-service failure")
        return {
            "politica_tratamiento_version": "2026-01",
            "politica_tratamiento_url": "https://entidad.example/privacidad",
            "politica_tratamiento_texto": "Aviso de privacidad de prueba",
            **self.params,
        }

    async def get_dependencia(self, *, tenant_slug: str, codigo: int) -> dict | None:
        self.dep_calls.append((tenant_slug, codigo))
        if self.dep_raise_error:
            raise TenantServiceError("simulated tenant-service failure")
        return self.dependencias.get(codigo)


def _make_mock_conn() -> AsyncMock:
    conn = AsyncMock()
    conn.fetchval.return_value = 1
    conn.execute.return_value = None
    conn.fetch.return_value = []
    conn.fetchrow.return_value = None
    # `async with conn.transaction()` (auditoría atómica): transaction() es una
    # llamada síncrona que devuelve un context manager asíncrono.
    conn.transaction = MagicMock(return_value=AsyncMock())
    return conn


def _make_fake_redis() -> AsyncMock:
    redis = AsyncMock()
    redis.xadd.return_value = b"1-0"
    return redis


def make_radicado_record(
    *,
    doc_id: uuid.UUID | None = None,
    tracking_number: str = "2024-DEMO-E-000001",
    doc_type: str = "E",
    year: int = 2024,
    sequence: int = 1,
    subject: str = "Solicitud de información",
    doc_class: str | None = "Derechos de Petición",
    origin_dept: str | None = None,
    dest_dept: str | None = "Dirección General",
    origin_dept_code: int | None = None,
    dest_dept_code: int | None = None,
    sender_name: str | None = "Juan Pérez",
    sender_entity: str | None = "Ciudadano",
    status: str = "registered",
    pages: int = 3,
    nivel_seguridad: int = 1,
    observations: str | None = None,
    registered_by: uuid.UUID | None = None,
    tenant_code: str = TENANT_CODE,
    due_date: date | None = None,
    metadata: dict | None = None,
    metadata_template_id: uuid.UUID | None = None,
    publicable: bool = False,
    publicado_at: datetime | None = None,
    flujo_dept: str | None = None,
    flujo_dept_code: int | None = None,
    flujo_usuario: uuid.UUID | None = None,
) -> dict:
    now = datetime.now(tz=UTC)
    return {
        "id": doc_id or uuid.uuid4(),
        "tracking_number": tracking_number,
        "doc_type": doc_type,
        "year": year,
        "sequence": sequence,
        "subject": subject,
        "doc_class": doc_class,
        "origin_dept": origin_dept,
        "dest_dept": dest_dept,
        "origin_dept_code": origin_dept_code,
        "dest_dept_code": dest_dept_code,
        "sender_name": sender_name,
        "sender_entity": sender_entity,
        "status": status,
        "pages": pages,
        "nivel_seguridad": nivel_seguridad,
        "observations": observations,
        "registered_by": registered_by or uuid.uuid4(),
        "registered_at": now,
        "updated_at": now,
        "tenant_code": tenant_code,
        "due_date": due_date,
        "metadata": metadata if metadata is not None else {},
        "metadata_template_id": metadata_template_id,
        # Esquema de publicación (Ley 1712 art. 12). El default es el real: un
        # radicado nace NO publicado, publicar es un acto aparte.
        "publicable": publicable,
        "publicado_at": publicado_at,
        # Ubicación actual (ADR-029): `flujo_*` las escribe workflow-service;
        # `ubicacion_*` son GENERADAS (par: flujo si lo hay, si no radicación).
        "flujo_dept": flujo_dept,
        "flujo_dept_code": flujo_dept_code,
        "flujo_usuario": flujo_usuario,
        "ubicacion_dept": flujo_dept if flujo_dept is not None else dest_dept,
        "ubicacion_dept_code": flujo_dept_code if flujo_dept is not None else dest_dept_code,
    }


def make_anexo_record(
    *,
    radicado_id: uuid.UUID | None = None,
    anexo_id: uuid.UUID | None = None,
) -> dict:
    now = datetime.now(tz=UTC)
    return {
        "id": anexo_id or uuid.uuid4(),
        "radicado_id": radicado_id or uuid.uuid4(),
        "file_id": uuid.uuid4(),
        "filename": "carta.pdf",
        "file_size": 12345,
        "mime_type": "application/pdf",
        "created_at": now,
        "tipo_anexo": None,
        "es_principal": False,
        "folios": None,
    }


def _override_conn(mock_conn: AsyncMock, fake_client: FakeTenantClient) -> None:
    async def _dep() -> AsyncGenerator[AsyncMock, None]:
        yield mock_conn

    app.dependency_overrides[get_tenant_conn] = _dep
    app.dependency_overrides[get_db_conn] = _dep
    # El endpoint de creación depende de get_tenant_client y get_redis (sin
    # lifespan no hay http_client ni redis reales); se sustituyen por dobles.
    app.dependency_overrides[get_tenant_client] = lambda: fake_client
    app.dependency_overrides[get_redis] = _make_fake_redis


@pytest_asyncio.fixture(scope="function")
async def client() -> AsyncGenerator[AsyncClient, None]:
    mock_conn = _make_mock_conn()
    _override_conn(mock_conn, FakeTenantClient())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def client_with_conn() -> AsyncGenerator[tuple[AsyncClient, AsyncMock], None]:
    """Yields (client, mock_conn) so tests can configure mock_conn per-call."""
    mock_conn = _make_mock_conn()
    _override_conn(mock_conn, FakeTenantClient())

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, mock_conn

    app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def client_with_deps() -> (
    AsyncGenerator[tuple[AsyncClient, AsyncMock, FakeTenantClient], None]
):
    """Yields (client, mock_conn, fake_tenant_client) para tests de RF-RAD-04."""
    mock_conn = _make_mock_conn()
    fake_client = FakeTenantClient()
    _override_conn(mock_conn, fake_client)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, mock_conn, fake_client

    app.dependency_overrides.clear()


@pytest_asyncio.fixture(scope="function")
async def client_with_redis() -> AsyncGenerator[tuple[AsyncClient, AsyncMock, AsyncMock], None]:
    """Yields (client, mock_conn, fake_redis) para tests de emisión de eventos."""
    mock_conn = _make_mock_conn()
    fake_redis = _make_fake_redis()
    _override_conn(mock_conn, FakeTenantClient())
    app.dependency_overrides[get_redis] = lambda: fake_redis

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, mock_conn, fake_redis

    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _reloj_de_la_base_fijo(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Las suites con `conn` simulado encadenan `fetchval` en un orden fijo; el reloj de la
    BASE que usa el outbox (`outbox._reloj_bd`) no debe consumir esa cadena. En el carril de
    integración (Postgres real) NO se sustituye: ahí se prueba el reloj de verdad."""
    if "integration" in request.node.nodeid:
        return
    from app.services import outbox

    async def _fijo(_conn: object) -> datetime:
        return datetime(2026, 10, 9, 12, 0, tzinfo=UTC)

    monkeypatch.setattr(outbox, "_reloj_bd", _fijo)


@pytest.fixture(autouse=True)
def _concordancia_sin_consulta(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """`exigir_concordancia` (ADR-032) consulta el catálogo con `conn.fetchval`; las suites con
    `conn` simulado encadenan esos valores en un orden fijo y no deben pagarla. Se ejercita
    sin sustituir en `test_dependencia_por_codigo.py` y en el carril de integración."""
    nodeid = request.node.nodeid
    if "integration" in nodeid or "test_dependencia_por_codigo" in nodeid:
        return

    async def _nada(*_a: object, **_k: object) -> None:
        return None

    monkeypatch.setattr("app.routers.documents.exigir_concordancia", _nada)
