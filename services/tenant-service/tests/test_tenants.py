# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""
Tests for tenant-service endpoints.

The database connection is fully mocked — no PostgreSQL instance required.
Repository calls are intercepted by patching service methods or configuring
the mock_conn return values.
"""

import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from tests.conftest import INTERNAL_HEADERS, make_tenant_record

# ---------------------------------------------------------------------------
# POST /api/v1/tenants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_tenant_success(client_with_conn: tuple[AsyncClient, AsyncMock]) -> None:
    """Creating a tenant returns 201 with UUID and tracking code."""
    client, mock_conn = client_with_conn
    record = make_tenant_record(slug="icetex", name="Instituto ICETEX", code="ICETEX")

    # exists_slug → None (not taken), exists_code → None (not taken)
    # fetchrow for INSERT RETURNING → record
    # execute for CREATE SCHEMA → None
    mock_conn.fetchval.side_effect = [None, None]
    mock_conn.fetchrow.return_value = record

    response = await client.post(
        "/api/v1/tenants",
        json={"slug": "icetex", "name": "Instituto ICETEX", "code": "ICETEX"},
        headers=INTERNAL_HEADERS,
    )

    assert response.status_code == 201
    body = response.json()
    assert "id" in body
    assert body["slug"] == "icetex"
    assert body["code"] == "ICETEX"
    assert body["status"] == "active"


@pytest.mark.asyncio
async def test_create_tenant_duplicate_slug(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """409 is returned when slug already exists."""
    client, mock_conn = client_with_conn

    # exists_slug returns truthy → slug taken
    mock_conn.fetchval.return_value = 1

    response = await client.post(
        "/api/v1/tenants",
        json={"slug": "icetex", "name": "Instituto ICETEX", "code": "ICETEX2"},
        headers=INTERNAL_HEADERS,
    )

    assert response.status_code == 409
    body = response.json()
    assert body["detail"]["error"] == "duplicate_slug"


@pytest.mark.asyncio
async def test_create_tenant_duplicate_code(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """409 is returned when code already exists (slug is free but code is taken)."""
    client, mock_conn = client_with_conn

    # First fetchval (exists_slug) → None (free), second (exists_code) → 1 (taken)
    mock_conn.fetchval.side_effect = [None, 1]

    response = await client.post(
        "/api/v1/tenants",
        json={"slug": "new-inst", "name": "New Institution", "code": "ICETEX"},
        headers=INTERNAL_HEADERS,
    )

    assert response.status_code == 409
    body = response.json()
    assert body["detail"]["error"] == "duplicate_code"


@pytest.mark.asyncio
async def test_create_tenant_invalid_slug(client: AsyncClient) -> None:
    """422 is returned when slug contains invalid characters."""
    response = await client.post(
        "/api/v1/tenants",
        json={"slug": "Mi Institución", "name": "Test", "code": "TEST"},
        headers=INTERNAL_HEADERS,
    )
    assert response.status_code == 422


@pytest.mark.asyncio
async def test_create_tenant_invalid_code(client: AsyncClient) -> None:
    """422 is returned when code contains lowercase or special characters."""
    response = await client.post(
        "/api/v1/tenants",
        json={"slug": "demo", "name": "Test", "code": "te st!"},
        headers=INTERNAL_HEADERS,
    )
    assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /api/v1/tenants
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_tenants(client_with_conn: tuple[AsyncClient, AsyncMock]) -> None:
    """List endpoint returns array and X-Total-Count header."""
    client, mock_conn = client_with_conn
    records = [
        make_tenant_record(slug="icetex", code="ICETEX"),
        make_tenant_record(slug="bogota", code="BOG"),
    ]
    # fetch → rows, fetchval → total count
    mock_conn.fetch.return_value = records
    mock_conn.fetchval.return_value = 2

    response = await client.get("/api/v1/tenants", headers=INTERNAL_HEADERS)

    assert response.status_code == 200
    assert response.headers["x-total-count"] == "2"
    body = response.json()
    assert isinstance(body, list)
    assert len(body) == 2


@pytest.mark.asyncio
async def test_list_tenants_with_status_filter(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Status filter is passed through to the query."""
    client, mock_conn = client_with_conn
    mock_conn.fetch.return_value = [make_tenant_record(status="inactive")]
    mock_conn.fetchval.return_value = 1

    response = await client.get("/api/v1/tenants?status=inactive", headers=INTERNAL_HEADERS)

    assert response.status_code == 200
    assert response.headers["x-total-count"] == "1"
    body = response.json()
    assert body[0]["status"] == "inactive"


# ---------------------------------------------------------------------------
# GET /api/v1/tenants/{tenant_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_tenant_by_id(client_with_conn: tuple[AsyncClient, AsyncMock]) -> None:
    """GET by UUID returns 200 with tenant data."""
    client, mock_conn = client_with_conn
    tenant_id = uuid.uuid4()
    record = make_tenant_record(slug="icetex", code="ICETEX", tenant_id=tenant_id)
    mock_conn.fetchrow.return_value = record

    response = await client.get(f"/api/v1/tenants/{tenant_id}", headers=INTERNAL_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(tenant_id)
    assert body["slug"] == "icetex"


@pytest.mark.asyncio
async def test_get_tenant_not_found(client_with_conn: tuple[AsyncClient, AsyncMock]) -> None:
    """GET with a non-existent UUID returns 404."""
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = None

    response = await client.get(f"/api/v1/tenants/{uuid.uuid4()}", headers=INTERNAL_HEADERS)

    assert response.status_code == 404
    body = response.json()
    assert body["detail"]["error"] == "tenant_not_found"


# ---------------------------------------------------------------------------
# GET /api/v1/tenants/by-slug/{slug}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_tenant_by_slug(client_with_conn: tuple[AsyncClient, AsyncMock]) -> None:
    """GET by slug returns 200 with matching tenant."""
    client, mock_conn = client_with_conn
    record = make_tenant_record(slug="icetex", code="ICETEX")
    mock_conn.fetchrow.return_value = record

    response = await client.get("/api/v1/tenants/by-slug/icetex", headers=INTERNAL_HEADERS)

    assert response.status_code == 200
    body = response.json()
    assert body["slug"] == "icetex"


@pytest.mark.asyncio
async def test_get_tenant_by_slug_not_found(
    client_with_conn: tuple[AsyncClient, AsyncMock],
    headers=INTERNAL_HEADERS,
) -> None:
    """GET by slug returns 404 when slug is unknown."""
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = None

    response = await client.get("/api/v1/tenants/by-slug/unknown-slug", headers=INTERNAL_HEADERS)

    assert response.status_code == 404
    assert response.json()["detail"]["error"] == "tenant_not_found"


# ---------------------------------------------------------------------------
# PATCH /api/v1/tenants/{tenant_id}
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_tenant(client_with_conn: tuple[AsyncClient, AsyncMock]) -> None:
    """PATCH updates name and status, returns updated tenant."""
    client, mock_conn = client_with_conn
    tenant_id = uuid.uuid4()
    updated_record = make_tenant_record(
        slug="icetex",
        name="ICETEX Updated",
        code="ICETEX",
        status="inactive",
        tenant_id=tenant_id,
    )
    mock_conn.fetchrow.return_value = updated_record

    response = await client.patch(
        f"/api/v1/tenants/{tenant_id}",
        json={"name": "ICETEX Updated", "status": "inactive"},
        headers=INTERNAL_HEADERS,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["name"] == "ICETEX Updated"
    assert body["status"] == "inactive"


@pytest.mark.asyncio
async def test_update_tenant_slug_immutable(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """slug in PATCH body is ignored — the endpoint does not accept it."""
    client, mock_conn = client_with_conn
    tenant_id = uuid.uuid4()
    original_record = make_tenant_record(slug="icetex", code="ICETEX", tenant_id=tenant_id)
    mock_conn.fetchrow.return_value = original_record

    response = await client.patch(
        f"/api/v1/tenants/{tenant_id}",
        # TenantUpdate schema has no slug field; extra fields are ignored by Pydantic
        json={"slug": "hacked-slug", "name": "New Name"},
        headers=INTERNAL_HEADERS,
    )

    assert response.status_code == 200
    # The slug in the response must remain unchanged
    assert response.json()["slug"] == "icetex"


@pytest.mark.asyncio
async def test_update_tenant_not_found(client_with_conn: tuple[AsyncClient, AsyncMock]) -> None:
    """PATCH on non-existent tenant returns 404."""
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = None

    response = await client.patch(
        f"/api/v1/tenants/{uuid.uuid4()}",
        json={"name": "Does Not Matter"},
        headers=INTERNAL_HEADERS,
    )

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health_check(client: AsyncClient) -> None:
    """Health endpoint returns 200 with structured status."""
    response = await client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body["database"] == "ok"
    assert body["status"] == "ok"


# ---------------------------------------------------------------------------
# D-02 — el registro de instituciones está sellado servicio-a-servicio
#
# `public.tenants` es el registro de TODAS las entidades y estas cinco rutas lo
# leen y lo escriben con una conexión SIN `search_path` de tenant. Su único
# control era no estar en la tabla de ruteo del gateway — una ausencia, no un
# control, y un salto de ruta `..` la atravesaba (cerrado en paralelo en
# `api-gateway/app/middleware/path_guard.py`; las dos capas son independientes
# a propósito).
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("metodo", "ruta"),
    [
        ("post", "/api/v1/tenants"),
        ("get", "/api/v1/tenants"),
        ("get", "/api/v1/tenants/by-slug/icetex"),
        ("get", "/api/v1/tenants/11111111-1111-1111-1111-111111111111"),
        ("patch", "/api/v1/tenants/11111111-1111-1111-1111-111111111111"),
    ],
)
@pytest.mark.asyncio
async def test_todas_las_rutas_exigen_token_interno(
    client_with_conn: tuple[AsyncClient, AsyncMock],
    metodo: str,
    ruta: str,
) -> None:
    """Las CINCO rutas, no una muestra: el gate está a nivel de router.

    Se parametrizan todas a propósito. Un test sobre una sola ruta dejaría que
    una futura excepción ruta-a-ruta pasara desapercibida, que es exactamente
    la forma en que este servicio perdió la autorización la primera vez.
    """
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = make_tenant_record()

    # `AsyncClient.get` no acepta `json=`; se manda solo donde el método lo lleva.
    kwargs = {"json": {}} if metodo in {"post", "patch"} else {}
    response = await getattr(client, metodo)(ruta, **kwargs)

    # 401 y no 422: el gate del router corre ANTES de validar el cuerpo, así que
    # un POST sin payload válido tampoco filtra información sobre el esquema.
    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "missing_internal_token"


@pytest.mark.asyncio
async def test_token_incorrecto_es_403(client_with_conn: tuple[AsyncClient, AsyncMock]) -> None:
    """Token presente pero equivocado: 403, distinto del 401 de token ausente."""
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = make_tenant_record()

    response = await client.get(
        "/api/v1/tenants",
        headers={"X-Internal-Token": "token-de-otro"},
    )

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "invalid_internal_token"


@pytest.mark.asyncio
async def test_sin_token_configurado_rechaza_todo(
    client_with_conn: tuple[AsyncClient, AsyncMock],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-closed: un despliegue que no configure el token deja el registro inalcanzable.

    Es el estado por DEFECTO y es el correcto: hoy no existe ningún consumidor
    legítimo por HTTP (el alta la hace `scripts/init_tenant.py` escribiendo
    directo a la BD). Un default que abriera el registro sería fail-open.
    """
    from app.core import config as cfg_module

    monkeypatch.setattr(cfg_module.settings, "internal_service_token", "")
    client, _ = client_with_conn

    response = await client.get(
        "/api/v1/tenants",
        headers={"X-Internal-Token": "cualquiera"},
    )

    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "internal_access_not_configured"
