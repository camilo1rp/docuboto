# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""`GET /api/v1/internal/documentos/radicados/{ref}/huella` (AREA-04-09 Media-2).

signature-service fija esta huella —SHA-256 del PDF VIGENTE del radicado, tal como
la CALCULÓ storage-service al subirlo— al abrir una cadena de firma y la relee
antes de cada firma. Aquí: el gate D-02 (`X-Internal-Token`), la forma de la
respuesta y el fail-closed (404 / 409 / 503). El SQL de «PDF vigente» se prueba
contra Postgres real en `tests/integration/test_internal_huella_radicado_integration.py`.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest

from app.clients import storage_client
from app.core import config as cfg_module
from tests.conftest import TENANT_SLUG

TOKEN = "test-document-internal-token-xxx"
HEADERS = {"X-Tenant-Slug": TENANT_SLUG, "X-Internal-Token": TOKEN}
REF = "2024-DEMO-E-000001"
_PATH = f"/api/v1/internal/documentos/radicados/{REF}/huella"
HUELLA = "AB" * 32
RAD = {"id": uuid.uuid4(), "tracking_number": REF}
ANEXO = {"id": uuid.uuid4(), "file_id": uuid.uuid4(), "filename": "oficio.pdf"}


@pytest.fixture(autouse=True)
def _token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cfg_module.settings, "internal_service_token", TOKEN)


def _storage(monkeypatch, *, huella=HUELLA, error=None) -> AsyncMock:
    mock = AsyncMock(return_value=huella, side_effect=error)
    monkeypatch.setattr(storage_client, "fetch_file_sha256", mock)
    return mock


@pytest.mark.asyncio
async def test_sin_token_o_con_token_malo_no_entra(client_with_conn) -> None:
    client, conn = client_with_conn
    assert (await client.get(_PATH, headers={"X-Tenant-Slug": TENANT_SLUG})).status_code == 401
    assert (
        await client.get(_PATH, headers={**HEADERS, "X-Internal-Token": "otro"})
    ).status_code == 403
    conn.fetchrow.assert_not_called()  # ni siquiera se consulta la base


@pytest.mark.asyncio
async def test_sin_token_configurado_falla_cerrado(client_with_conn, monkeypatch) -> None:
    client, _ = client_with_conn
    monkeypatch.setattr(cfg_module.settings, "internal_service_token", "")
    assert (await client.get(_PATH, headers=HEADERS)).status_code == 401


@pytest.mark.asyncio
async def test_devuelve_la_huella_de_storage_en_minuscula(client_with_conn, monkeypatch) -> None:
    client, conn = client_with_conn
    conn.fetchrow.side_effect = [RAD, ANEXO]
    storage = _storage(monkeypatch)
    resp = await client.get(_PATH, headers=HEADERS)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["huella_sha256"] == HUELLA.lower()
    assert body["tracking_number"] == REF
    assert body["file_id"] == str(ANEXO["file_id"])
    # La huella la pone STORAGE (la calculó al subir), no una columna de este servicio.
    storage.assert_awaited_once()
    assert storage.await_args.kwargs["file_id"] == ANEXO["file_id"]
    assert storage.await_args.kwargs["tenant_slug"] == TENANT_SLUG
    # La referencia viaja como parámetro, jamás interpolada en el SQL.
    for llamada in conn.fetchrow.await_args_list:
        assert REF not in llamada.args[0]


@pytest.mark.asyncio
async def test_radicado_inexistente_404(client_with_conn, monkeypatch) -> None:
    client, conn = client_with_conn
    conn.fetchrow.side_effect = [None]
    _storage(monkeypatch, error=RuntimeError("no debe llamarse"))
    resp = await client.get(_PATH, headers=HEADERS)
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "radicado_not_found"


@pytest.mark.asyncio
async def test_radicado_sin_pdf_409(client_with_conn, monkeypatch) -> None:
    client, conn = client_with_conn
    conn.fetchrow.side_effect = [RAD, None]
    _storage(monkeypatch, error=RuntimeError("no debe llamarse"))
    resp = await client.get(_PATH, headers=HEADERS)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "radicado_sin_documento"
    assert "principal" in resp.json()["detail"]["detail"]


@pytest.mark.asyncio
async def test_storage_no_conoce_el_fichero_409(client_with_conn, monkeypatch) -> None:
    client, conn = client_with_conn
    conn.fetchrow.side_effect = [RAD, ANEXO]
    _storage(monkeypatch, huella=None)
    resp = await client.get(_PATH, headers=HEADERS)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "anexo_sin_huella"


@pytest.mark.asyncio
async def test_storage_caido_503_nunca_una_huella_inventada(client_with_conn, monkeypatch) -> None:
    client, conn = client_with_conn
    conn.fetchrow.side_effect = [RAD, ANEXO]
    _storage(
        monkeypatch,
        error=storage_client.StorageContentError("storage-service → HTTP 500: b'SECRETO db-7:5432'"),
    )
    resp = await client.get(_PATH, headers=HEADERS)
    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "huella_no_verificable"
    # El cuerpo/host del colaborador no viaja a quien consulta.
    assert "SECRETO" not in resp.text and "db-7" not in resp.text
