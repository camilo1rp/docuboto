# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Las rutas internas no existen sin token. Un `X-User-Id` de cliente no autentica."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from storage.main import app

HOLD = "/api/v1/preservacion/legal-hold-internal/fijar"
USER = "11111111-1111-1111-1111-111111111111"


async def _post(ruta: str, cabeceras: dict[str, str]):
    transporte = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transporte, base_url="http://test") as client:
        return await client.post(ruta, json={}, headers=cabeceras)


@pytest.mark.asyncio
async def test_ruta_interna_sin_token_da_404() -> None:
    r = await _post(HOLD, {"X-Tenant-Slug": "demo", "X-User-Id": USER})
    assert r.status_code == 404
    assert r.json().get("error") == "route_not_found"


@pytest.mark.asyncio
async def test_x_user_id_sin_bearer_no_autentica() -> None:
    r = await _post("/api/v1/storage/files", {"X-Tenant-Slug": "demo", "X-User-Id": USER})
    # Sin Bearer el middleware no corta; el handler exige identidad o permiso.
    assert r.status_code != 200
