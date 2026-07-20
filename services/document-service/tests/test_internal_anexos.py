# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de `POST /api/v1/internal/documentos/anexos` (E10 debt, cableado del
AIP OAIS al cierre del expediente) — primer endpoint interno de
document-service (`app/routers/internal.py`).

Gate D-02 (`X-Internal-Token`, fail-closed, `app/core/internal_auth.py`) +
inventario TOTAL batch de anexos por `radicado_id`, SIN el gate de clearance
que sí aplica `GET /documents/{id}/anexos` (RF-SEG-08, no-read-up): el
empaquetado del AIP es un acto de SISTEMA, no una lectura de usuario. La
inalcanzabilidad EXTERNA de este endpoint (el gateway no enruta
`/api/v1/internal/*` y, aunque lo hiciera, `X-Internal-Token` no viaja en
`FORWARDED_REQUEST_HEADERS`) se prueba en `api-gateway/tests/test_gateway.py`,
no aquí. El aislamiento REAL por tenant (dos schemas físicos de Postgres) se
prueba en `tests/integration/test_internal_anexos_tenant_isolation.py`; aquí
solo se confirma que la query del repositorio filtra ÚNICAMENTE por
`radicado_id` (sin JOIN a `radicados`/clearance) y que el router no exige
`X-User-Id`.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.core import config as cfg_module
from app.repositories.document_repository import DocumentRepository
from tests.conftest import TENANT_SLUG, make_anexo_record

TEST_INTERNAL_TOKEN = "test-document-internal-token-xxx"
INTERNAL_HEADERS = {"X-Tenant-Slug": TENANT_SLUG, "X-Internal-Token": TEST_INTERNAL_TOKEN}
_PATH = "/api/v1/internal/documentos/anexos"


@pytest.fixture(autouse=True)
def _internal_token_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg_module.settings, "internal_service_token", TEST_INTERNAL_TOKEN)


# ---------------------------------------------------------------------------
# Gate D-02: X-Internal-Token
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_without_token_is_401(client_with_conn) -> None:
    client, _mock_conn = client_with_conn

    resp = await client.post(
        _PATH,
        json={"radicado_ids": [str(uuid.uuid4())]},
        headers={"X-Tenant-Slug": TENANT_SLUG},  # sin X-Internal-Token
    )

    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "missing_internal_token"


@pytest.mark.asyncio
async def test_with_wrong_token_is_403(client_with_conn) -> None:
    client, _mock_conn = client_with_conn

    resp = await client.post(
        _PATH,
        json={"radicado_ids": [str(uuid.uuid4())]},
        headers={"X-Tenant-Slug": TENANT_SLUG, "X-Internal-Token": "wrong-token"},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "invalid_internal_token"


@pytest.mark.asyncio
async def test_token_not_configured_is_401(
    client_with_conn, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-closed: `INTERNAL_SERVICE_TOKEN` vacío rechaza incluso con header
    correcto — jamás hay un modo "abierto" por omisión."""
    client, _mock_conn = client_with_conn
    monkeypatch.setattr(cfg_module.settings, "internal_service_token", "")

    resp = await client.post(
        _PATH, json={"radicado_ids": [str(uuid.uuid4())]}, headers=INTERNAL_HEADERS
    )

    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "internal_access_not_configured"


def test_require_internal_token_uses_constant_time_comparison(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Remediación de auditoría (Baja): `require_internal_token` compara con
    `hmac.compare_digest`, no `!=` (side-channel de temporización) — probado
    DIRECTAMENTE (sin HTTP) para cubrir tokens de LONGITUD DISTINTA, que
    `compare_digest` maneja sin excepción y que ningún test HTTP existente
    ejercita a propósito. El fail-closed (401 sin config/sin header) no
    cambia — solo esta comparación final."""
    from fastapi import HTTPException

    from app.core.internal_auth import require_internal_token

    monkeypatch.setattr(cfg_module.settings, "internal_service_token", TEST_INTERNAL_TOKEN)

    require_internal_token(x_internal_token=TEST_INTERNAL_TOKEN)  # no lanza

    with pytest.raises(HTTPException) as exc_info:
        require_internal_token(x_internal_token="x" * len(TEST_INTERNAL_TOKEN))
    assert exc_info.value.status_code == 403

    with pytest.raises(HTTPException) as exc_info:
        require_internal_token(x_internal_token="short")
    assert exc_info.value.status_code == 403

    with pytest.raises(HTTPException) as exc_info:
        require_internal_token(x_internal_token=TEST_INTERNAL_TOKEN + "-extra-suffix")
    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_no_clearance_gate_request_without_x_user_id_succeeds(client_with_conn) -> None:
    """Acto de SISTEMA: a diferencia de las rutas RBAC del servicio (que
    resuelven `X-User-Id`), este endpoint no lo exige — solo el token
    interno gatea el acceso."""
    client, mock_conn = client_with_conn
    mock_conn.fetch.return_value = []

    resp = await client.post(
        _PATH, json={"radicado_ids": [str(uuid.uuid4())]}, headers=INTERNAL_HEADERS
    )

    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Inventario batch — forma de la respuesta
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_batch_returns_anexos_grouped_by_radicado_id(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    radicado_a = uuid.uuid4()
    radicado_b = uuid.uuid4()  # sin anexos -> no debe aparecer como clave
    anexo_a1 = make_anexo_record(radicado_id=radicado_a)
    anexo_a2 = make_anexo_record(radicado_id=radicado_a)
    mock_conn.fetch.return_value = [anexo_a1, anexo_a2]

    resp = await client.post(
        _PATH,
        json={"radicado_ids": [str(radicado_a), str(radicado_b)]},
        headers=INTERNAL_HEADERS,
    )

    assert resp.status_code == 200
    body = resp.json()
    assert str(radicado_b) not in body
    assert len(body[str(radicado_a)]) == 2
    returned_file_ids = {item["file_id"] for item in body[str(radicado_a)]}
    assert returned_file_ids == {str(anexo_a1["file_id"]), str(anexo_a2["file_id"])}
    for item in body[str(radicado_a)]:
        assert set(item.keys()) == {"file_id", "filename"}


@pytest.mark.asyncio
async def test_radicado_without_anexos_is_empty_dict_not_error(client_with_conn) -> None:
    """Ningún anexo para NINGÚN `radicado_id` del lote -> `{}` (200), nunca
    un error — el llamador (`document_client.fetch_anexos_internal`) lo
    trata como "sin anexos digitales", no como un fallo."""
    client, mock_conn = client_with_conn
    mock_conn.fetch.return_value = []

    resp = await client.post(
        _PATH, json={"radicado_ids": [str(uuid.uuid4())]}, headers=INTERNAL_HEADERS
    )

    assert resp.status_code == 200
    assert resp.json() == {}


@pytest.mark.asyncio
async def test_empty_radicado_ids_is_rejected_by_schema(client_with_conn) -> None:
    """`AnexosInternalRequest.radicado_ids` exige `min_length=1` — un batch
    vacío es un error de uso del llamador, 422 antes de tocar la BD."""
    client, mock_conn = client_with_conn

    resp = await client.post(_PATH, json={"radicado_ids": []}, headers=INTERNAL_HEADERS)

    assert resp.status_code == 422
    mock_conn.fetch.assert_not_called()


# ---------------------------------------------------------------------------
# Repositorio: la query filtra SOLO por radicado_id (sin clearance/JOIN)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_repository_filters_by_radicado_ids_only_no_clearance_join() -> None:
    """Deliberadamente distinto de `_get_anexos`/`list_anexos_for_document`
    (no-read-up, RF-SEG-08): SIN JOIN a `radicados` ni filtro de
    `nivel_seguridad` — inventario TOTAL de sistema, ver el docstring de
    `list_anexos_for_radicados_internal`."""
    mock_conn = AsyncMock()
    mock_conn.fetch.return_value = []
    repo = DocumentRepository(mock_conn, TENANT_SLUG)
    ids = [uuid.uuid4(), uuid.uuid4()]

    result = await repo.list_anexos_for_radicados_internal(ids)

    assert result == {}
    mock_conn.fetch.assert_awaited_once()
    sql = mock_conn.fetch.await_args.args[0]
    assert "WHERE radicado_id = ANY($1" in sql
    assert "JOIN" not in sql.upper()
    assert mock_conn.fetch.await_args.args[1] == ids


@pytest.mark.asyncio
async def test_repository_empty_input_short_circuits_without_query() -> None:
    mock_conn = AsyncMock()
    repo = DocumentRepository(mock_conn, TENANT_SLUG)

    result = await repo.list_anexos_for_radicados_internal([])

    assert result == {}
    mock_conn.fetch.assert_not_called()
