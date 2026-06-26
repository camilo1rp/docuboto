# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de vistos buenos secuenciales (paridad legado)."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import AsyncClient

from tests.conftest import TENANT_HEADERS

NOW = datetime.now(tz=UTC)
RAD = uuid.uuid4()
REV1 = uuid.uuid4()
REV2 = uuid.uuid4()


def _row(orden, revisor, estado="pendiente") -> dict:
    return {
        "id": uuid.uuid4(),
        "orden": orden,
        "revisor_id": revisor,
        "estado": estado,
        "comentario": None,
        "decidido_at": None,
    }


@pytest.mark.asyncio
async def test_crear_cadena(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    # exists → None (fetchval), crear (execute), get → list (fetch)
    mock_conn.fetchval.return_value = None
    mock_conn.fetch.return_value = [_row(1, REV1), _row(2, REV2)]

    resp = await client.post(
        f"/api/v1/workflows/{RAD}/vistos-buenos",
        json={"revisores": [str(REV1), str(REV2)]},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["estado"] == "en_revision"
    assert len(body["items"]) == 2


@pytest.mark.asyncio
async def test_crear_cadena_duplicada_409(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = 1  # exists → True
    resp = await client.post(
        f"/api/v1/workflows/{RAD}/vistos-buenos",
        json={"revisores": [str(REV1)]},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "cadena_existente"


@pytest.mark.asyncio
async def test_decidir_sin_user_id_400(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """D-06: X-User-Id es obligatorio en decidir; sin él el router devuelve 400."""
    client, _, _ = client_with_conn
    resp = await client.post(
        f"/api/v1/workflows/{RAD}/vistos-buenos/decidir",
        json={"aprobar": True},
        headers={"X-Tenant-Slug": "demo"},  # sin X-User-Id
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "missing_user"


@pytest.mark.asyncio
async def test_decidir_not_reviewer_403(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    """D-06: el actor debe pertenecer a la cadena; si no → 403 not_a_reviewer."""
    client, mock_conn, _ = client_with_conn
    stranger = uuid.uuid4()
    mock_conn.fetchval.return_value = None  # is_reviewer → False (no está en la cadena)
    resp = await client.post(
        f"/api/v1/workflows/{RAD}/vistos-buenos/decidir",
        json={"aprobar": True},
        headers={"X-Tenant-Slug": "demo", "X-User-Id": str(stranger)},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "not_a_reviewer"


@pytest.mark.asyncio
async def test_decidir_fuera_de_turno_403(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    # active → paso de REV1; actor distinto
    mock_conn.fetchrow.return_value = {
        "id": uuid.uuid4(),
        "orden": 1,
        "revisor_id": REV1,
        "estado": "pendiente",
    }
    resp = await client.post(
        f"/api/v1/workflows/{RAD}/vistos-buenos/decidir",
        json={"aprobar": True},
        headers={**TENANT_HEADERS, "X-User-Id": str(REV2)},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "no_es_su_turno"


@pytest.mark.asyncio
async def test_decidir_aprueba_su_turno(
    client_with_conn: tuple[AsyncClient, AsyncMock, MagicMock],
) -> None:
    client, mock_conn, _ = client_with_conn
    # active (fetchrow) → REV1; decide (execute); get → list (fetch) con paso 1 aprobado
    mock_conn.fetchrow.return_value = {
        "id": uuid.uuid4(),
        "orden": 1,
        "revisor_id": REV1,
        "estado": "pendiente",
    }
    mock_conn.fetch.return_value = [_row(1, REV1, "aprobado"), _row(2, REV2)]

    resp = await client.post(
        f"/api/v1/workflows/{RAD}/vistos-buenos/decidir",
        json={"aprobar": True, "comentario": "Ok"},
        headers={**TENANT_HEADERS, "X-User-Id": str(REV1)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["estado"] == "en_revision"  # falta REV2
    assert body["items"][0]["estado"] == "aprobado"
