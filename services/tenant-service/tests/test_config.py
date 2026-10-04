# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de configuración por tenant (E14): festivos, días hábiles, parámetros.

Las escrituras (POST/DELETE festivos, seed, PUT params) exigen `USUA_PERM_ADMIN`
(hallazgo Crítico de la auditoría 2026-07: el servicio no tenía NINGÚN gate) y
dejan asiento Canal A (`public.audit_log`); se patchea `app.core.audit._audit.append`
para no ejercer la cadena de hash real. Los GET (festivos, business-days/calculate,
params) quedan solo autenticados — el cálculo de plazos legales los consume para
cualquier usuario radicador, no solo administradores.
"""

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.database import get_tenant_conn
from app.main import app

_HDRS = {"X-User-Id": str(uuid.uuid4()), "X-Tenant-Slug": "demo"}


def _holiday(
    *,
    holiday_id: uuid.UUID | None = None,
    fecha: date = date(2026, 1, 1),
    descripcion: str = "Año Nuevo",
    tipo: str = "festivo",
    is_national: bool = True,
) -> dict:
    return {
        "id": holiday_id or uuid.uuid4(),
        "fecha": fecha,
        "descripcion": descripcion,
        "tipo": tipo,
        "is_national": is_national,
    }


@asynccontextmanager
async def _noop_tx() -> AsyncGenerator[None, None]:
    yield


@pytest_asyncio.fixture(scope="function")
async def client_conn() -> AsyncGenerator[tuple[AsyncClient, AsyncMock], None]:
    conn = AsyncMock()
    conn.fetch.return_value = []
    conn.fetchrow.return_value = None
    # Gate RBAC (USUA_PERM_ADMIN) concedido por defecto.
    conn.fetchval.return_value = True
    conn.execute.return_value = "DELETE 0"
    conn.transaction = MagicMock(side_effect=lambda *a, **k: _noop_tx())

    async def override() -> AsyncGenerator[AsyncMock, None]:
        yield conn

    app.dependency_overrides[get_tenant_conn] = override
    with patch("app.core.audit._audit.append", new=AsyncMock(return_value="h")) as audit_append:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            ac.audit_append = audit_append  # type: ignore[attr-defined]
            yield ac, conn
    app.dependency_overrides.clear()


# --- lecturas: solo autenticadas, sin gate de rol -----------------------------


@pytest.mark.asyncio
async def test_list_holidays_no_role_gate(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetch.return_value = [_holiday()]
    resp = await client.get("/api/v1/config/holidays")
    assert resp.status_code == 200
    assert len(resp.json()) == 1


@pytest.mark.asyncio
async def test_calculate_business_days_no_role_gate(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, _ = client_conn
    resp = await client.get(
        "/api/v1/business-days/calculate", params={"from": "2026-01-01", "days": 5}
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_get_params_no_role_gate(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, _ = client_conn
    resp = await client.get("/api/v1/config/params")
    assert resp.status_code == 200


# --- gate RBAC ------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_holiday_forbidden_without_permission(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, conn = client_conn
    conn.fetchval.return_value = False
    resp = await client.post(
        "/api/v1/config/holidays",
        json={"fecha": "2026-01-01", "descripcion": "Año Nuevo"},
        headers=_HDRS,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_create_holiday_forbidden_without_identity(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, _ = client_conn
    resp = await client.post(
        "/api/v1/config/holidays", json={"fecha": "2026-01-01", "descripcion": "Año Nuevo"}
    )
    assert resp.status_code == 403


# --- festivos: create/delete/seed --------------------------------------------


@pytest.mark.asyncio
async def test_create_holiday(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetchrow.return_value = _holiday()
    resp = await client.post(
        "/api/v1/config/holidays",
        json={"fecha": "2026-01-01", "descripcion": "Año Nuevo"},
        headers=_HDRS,
    )
    assert resp.status_code == 201
    assert client.audit_append.await_args.kwargs["action"] == "config.festivo_creado"
    assert client.audit_append.await_args.kwargs["actor"] == _HDRS["X-User-Id"]


@pytest.mark.asyncio
async def test_create_holiday_duplicate_409(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetchrow.side_effect = asyncpg.UniqueViolationError("dup")
    resp = await client.post(
        "/api/v1/config/holidays",
        json={"fecha": "2026-01-01", "descripcion": "Año Nuevo"},
        headers=_HDRS,
    )
    assert resp.status_code == 409
    client.audit_append.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_holiday(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.execute.return_value = "DELETE 1"
    resp = await client.delete(f"/api/v1/config/holidays/{uuid.uuid4()}", headers=_HDRS)
    assert resp.status_code == 204
    assert client.audit_append.await_args.kwargs["action"] == "config.festivo_eliminado"


@pytest.mark.asyncio
async def test_delete_holiday_not_found(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.execute.return_value = "DELETE 0"
    resp = await client.delete(f"/api/v1/config/holidays/{uuid.uuid4()}", headers=_HDRS)
    assert resp.status_code == 404
    client.audit_append.assert_not_awaited()


@pytest.mark.asyncio
async def test_seed_holidays(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.execute.return_value = "INSERT 0 1"
    resp = await client.post(
        "/api/v1/config/holidays/seed",
        json={"year_from": 2026, "year_to": 2026},
        headers=_HDRS,
    )
    assert resp.status_code == 202
    assert client.audit_append.await_args.kwargs["action"] == "config.festivos_sembrados"


@pytest.mark.asyncio
async def test_seed_holidays_invalid_range(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, _ = client_conn
    resp = await client.post(
        "/api/v1/config/holidays/seed",
        json={"year_from": 2027, "year_to": 2026},
        headers=_HDRS,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "invalid_range"


# --- parámetros ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_param(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, _ = client_conn
    resp = await client.put(
        "/api/v1/config/params/dias_respuesta_pqr", json={"value": 15}, headers=_HDRS
    )
    assert resp.status_code == 200
    assert resp.json()["value"] == 15
    assert client.audit_append.await_args.kwargs["action"] == "config.parametro_actualizado"
    assert client.audit_append.await_args.kwargs["object_ref"] == "dias_respuesta_pqr"


@pytest.mark.asyncio
async def test_set_param_asienta_antes_y_despues(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """AREA-10-14: el asiento lleva el valor vigente ANTES del cambio."""
    client, conn = client_conn
    # RBAC, lectura previa FOR UPDATE (jsonb llega como texto)
    conn.fetchval.side_effect = [True, "6"]
    resp = await client.put("/api/v1/config/params/consec_digits", json={"value": 8}, headers=_HDRS)
    assert resp.status_code == 200
    payload = client.audit_append.await_args.kwargs["payload"]
    assert payload["antes"] == 6
    assert payload["despues"] == 8
    assert payload["existia"] is True
    assert "FOR UPDATE" in conn.fetchval.await_args_list[1].args[0]


@pytest.mark.asyncio
async def test_set_param_nuevo_asienta_antes_nulo(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, conn = client_conn
    conn.fetchval.side_effect = [True, None]
    resp = await client.put("/api/v1/config/params/otro", json={"value": 1}, headers=_HDRS)
    assert resp.status_code == 200
    payload = client.audit_append.await_args.kwargs["payload"]
    assert payload["antes"] is None and payload["existia"] is False


@pytest.mark.asyncio
async def test_set_param_forbidden_without_permission(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, conn = client_conn
    conn.fetchval.return_value = False
    resp = await client.put(
        "/api/v1/config/params/dias_respuesta_pqr", json={"value": 15}, headers=_HDRS
    )
    assert resp.status_code == 403
