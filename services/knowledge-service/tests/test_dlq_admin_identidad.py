# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Contrato gateway -> knowledge del admin de la DLQ (AREA-10-10), revisión de testing-engineer.

El router nuevo autoriza por `X-User-Id`. Sus pruebas de unidad corren con el secreto de
identidad apagado (tests/conftest.py), así que NINGUNA ejerce el middleware: si alguien
quitara `IdentidadFirmadaMiddleware` de `app.main`, o el gateway dejara de firmar, la
DLQ de knowledge aceptaría una identidad forjada con solo poner la cabecera. Aquí se
enciende el secreto y se comprueba la puerta real, ruta por ruta.
"""

from __future__ import annotations

import pytest
from orpycamcp_common import identidad

BASE = "/api/v1/knowledge/admin/deadletter"
USER = "11111111-1111-1111-1111-111111111111"
EVT = "evt-1"
RUTAS = [
    ("GET", f"{BASE}/", None),
    ("GET", f"{BASE}/{EVT}", None),
    ("POST", f"{BASE}/{EVT}/replay", None),
    ("POST", f"{BASE}/{EVT}/discard", {"reason": "x"}),
]


@pytest.fixture(autouse=True)
def _secreto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("IDENTITY_SIGNING_SECRET", "s3cr3to-de-prueba-dlq")


@pytest.mark.asyncio
@pytest.mark.parametrize("metodo,ruta,cuerpo", RUTAS)
async def test_una_identidad_sin_firma_da_401_y_no_toca_la_base(
    client_with_conn, metodo: str, ruta: str, cuerpo
) -> None:
    client, conn = client_with_conn
    r = await client.request(
        metodo, ruta, json=cuerpo, headers={"X-Tenant-Slug": "demo", "X-User-Id": USER}
    )
    assert r.status_code == 401
    assert r.json()["error"] == "identidad_no_autentica"
    conn.fetchval.assert_not_called()
    conn.fetchrow.assert_not_called()
    conn.fetch.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("metodo,ruta,cuerpo", RUTAS)
async def test_una_identidad_firmada_por_el_gateway_pasa_al_gate_de_permiso(
    client_with_conn, metodo: str, ruta: str, cuerpo
) -> None:
    client, conn = client_with_conn
    conn.fetchval.return_value = False  # sin PERM_DLQ_ADMIN: 403 prueba que cruzó el middleware
    cabeceras = identidad.firmar({"X-Tenant-Slug": "demo", "X-User-Id": USER})
    r = await client.request(metodo, ruta, json=cuerpo, headers=cabeceras)
    assert r.status_code == 403
