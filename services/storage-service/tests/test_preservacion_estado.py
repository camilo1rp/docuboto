# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`GET /preservacion/estado` (AREA-12-04): ¿está encendida la preservación?

La pestaña Preservación mostraba «Sin AIP empaquetado, o sin visibilidad» con
la preservación APAGADA: un 404 neutro que hacía pasar por normal que nada se
empaqueta jamás. El estado es de INSTANCIA, así que no abre oráculo por
expediente: solo refleja `settings.preservation_enabled`.
"""

import pytest

from app.core.config import settings
from tests.conftest import TENANT_HEADERS


@pytest.mark.asyncio
@pytest.mark.parametrize("valor", [True, False])
async def test_el_estado_refleja_la_configuracion(client_with_conn, monkeypatch, valor) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = True  # gate de lectura concedido
    monkeypatch.setattr(settings, "preservation_enabled", valor)

    resp = await client.get("/api/v1/preservacion/estado", headers=TENANT_HEADERS)

    assert resp.status_code == 200
    assert resp.json() == {"enabled": valor}


@pytest.mark.asyncio
async def test_el_estado_exige_permiso_de_lectura(client_with_conn, monkeypatch) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = False  # gate denegado
    monkeypatch.setattr(settings, "preservation_enabled", True)

    resp = await client.get("/api/v1/preservacion/estado", headers=TENANT_HEADERS)

    assert resp.status_code == 403
    assert "enabled" not in resp.text


@pytest.mark.asyncio
async def test_el_estado_no_consulta_ningun_expediente(client_with_conn, monkeypatch) -> None:
    """Dato de instancia: ninguna lectura de datos más allá del gate RBAC."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = True
    monkeypatch.setattr(settings, "preservation_enabled", False)

    await client.get("/api/v1/preservacion/estado", headers=TENANT_HEADERS)

    mock_conn.fetchrow.assert_not_called()
    mock_conn.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_el_estado_sin_identidad_es_403_fail_closed(client_with_conn, monkeypatch) -> None:
    """Sin `X-User-Id` el gate niega SIN siquiera consultar permisos: ni el
    booleano de instancia ni el motivo se filtran a un llamador anónimo."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = True  # aunque la BD concediera, no se consulta
    monkeypatch.setattr(settings, "preservation_enabled", True)

    resp = await client.get(
        "/api/v1/preservacion/estado", headers={"X-Tenant-Slug": TENANT_HEADERS["X-Tenant-Slug"]}
    )

    assert resp.status_code == 403
    assert "enabled" not in resp.text
    mock_conn.fetchval.assert_not_called()
