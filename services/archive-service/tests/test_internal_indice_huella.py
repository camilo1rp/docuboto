# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""`GET /api/v1/internal/expedientes/{ref}/indice/huella` (AREA-04-09 Media-2).

signature-service fija la huella del ÍNDICE ELECTRÓNICO vigente del expediente al
abrir una cadena de firma sobre un `expediente` y la relee antes de cada firma
(antes firmaba el identificador). Router propio, gate D-02 (`X-Internal-Token`,
fail-closed). El SQL real se prueba en `tests/integration/test_internal_indice_huella.py`.
"""

from __future__ import annotations

import uuid

import pytest

from app.core.config import settings
from tests.conftest import TENANT_SLUG

TOKEN = "test-archive-internal-token-xxx"
HEADERS = {"X-Tenant-Slug": TENANT_SLUG, "X-Internal-Token": TOKEN}
REF = "EXP-2024-0001"
_PATH = f"/api/v1/internal/expedientes/{REF}/indice/huella"
EXP = {"id": uuid.uuid4(), "code": REF}
INDICE = {"version": 3, "estado": "firmado", "xml_sha256": "AB" * 32, "num_documentos": 7}


@pytest.fixture(autouse=True)
def _token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "internal_service_token", TOKEN)


@pytest.mark.asyncio
async def test_sin_token_o_con_token_malo_no_entra_y_no_toca_la_base(client_with_conn) -> None:
    client, conn = client_with_conn
    r = await client.get(_PATH, headers={"X-Tenant-Slug": TENANT_SLUG})
    assert r.status_code == 401 and r.json()["detail"]["error"] == "missing_internal_token"
    r = await client.get(_PATH, headers={**HEADERS, "X-Internal-Token": "otro"})
    assert r.status_code == 403 and r.json()["detail"]["error"] == "invalid_internal_token"
    conn.fetchrow.assert_not_called()
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_sin_token_configurado_falla_cerrado(client_with_conn, monkeypatch) -> None:
    client, _ = client_with_conn
    monkeypatch.setattr(settings, "internal_service_token", "")
    r = await client.get(_PATH, headers=HEADERS)
    assert r.status_code == 401
    assert r.json()["detail"]["error"] == "internal_access_not_configured"


@pytest.mark.asyncio
async def test_una_cabecera_de_usuario_no_sustituye_al_token(client_with_conn) -> None:
    """No es una ruta de usuario: ni JWT ni X-User-Id abren esta puerta."""
    client, _ = client_with_conn
    r = await client.get(
        _PATH, headers={"X-Tenant-Slug": TENANT_SLUG, "X-User-Id": str(uuid.uuid4())}
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_sin_tenant_400(client_with_conn) -> None:
    client, _ = client_with_conn
    r = await client.get(_PATH, headers={"X-Internal-Token": TOKEN})
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_devuelve_la_huella_canonica_del_indice_vigente(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.side_effect = [EXP, INDICE]
    r = await client.get(_PATH, headers=HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["huella_sha256"] == INDICE["xml_sha256"].lower()
    assert body["version"] == 3 and body["code"] == REF
    # La referencia viaja como parámetro; el índice es la ÚLTIMA versión.
    sqls = [c.args[0] for c in conn.fetchrow.await_args_list]
    assert all(REF not in s for s in sqls)
    assert "ORDER BY version DESC LIMIT 1" in sqls[1]


@pytest.mark.asyncio
async def test_expediente_inexistente_404(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.side_effect = [None]
    r = await client.get(_PATH, headers=HEADERS)
    assert r.status_code == 404 and r.json()["detail"]["error"] == "expediente_not_found"


@pytest.mark.asyncio
async def test_expediente_sin_indice_409(client_with_conn) -> None:
    client, conn = client_with_conn
    conn.fetchrow.side_effect = [EXP, None]
    r = await client.get(_PATH, headers=HEADERS)
    assert r.status_code == 409 and r.json()["detail"]["error"] == "indice_no_disponible"
