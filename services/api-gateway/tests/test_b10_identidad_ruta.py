# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El gateway reenvía el Bearer. Ya no firma `X-User-Id` (ADR-036 queda en el JWT)."""

from unittest.mock import AsyncMock

import httpx
import pytest
from orpycamcp_common import identidad

from .conftest import make_validate_response, wire_upstream

TOKEN = "Bearer valid-token"


async def _cabeceras_al_upstream(test_client, metodo: str, url: str) -> dict:
    client, http_client, _ = test_client
    http_client.get = AsyncMock(return_value=make_validate_response(valid=True))
    wire_upstream(http_client, httpx.Response(status_code=200, json={"ok": True}))
    await client.request(metodo, url, headers={"Authorization": TOKEN, "X-Tenant-Slug": "icetex"})
    return dict(http_client.build_request.call_args.kwargs["headers"])


@pytest.mark.asyncio
async def test_el_gateway_reenvia_el_bearer_sin_firma_de_persona(test_client):
    cab = await _cabeceras_al_upstream(test_client, "GET", "/api/v1/documents/?size=5&q=a")
    assert cab.get("Authorization") == TOKEN or cab.get("authorization") == TOKEN
    assert identidad.CABECERA_FIRMA not in cab
    assert "X-Identity-Signature" not in cab
