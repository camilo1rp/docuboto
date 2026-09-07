# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Las primeras pruebas de este servicio, y esa ausencia era parte del problema.

Fue el único servicio interno sin gate servicio-a-servicio, sin tope de tamaño y
corriendo como root. Un servicio sin suite no tiene dónde caerse cuando alguien
lo cambia, y por eso se quedó fuera de tres criterios que el resto sí aplicaba.
"""

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import settings
from app.main import app

pytestmark = pytest.mark.asyncio

_TOKEN = "token-de-pruebas"


@pytest.fixture
def con_token(monkeypatch):
    monkeypatch.setattr(settings, "internal_service_token", _TOKEN)


async def _cliente():
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


def _audio(n: int = 32) -> dict:
    return {"files": {"file": ("dictado.webm", b"\x00" * n, "audio/webm")}}


async def test_sin_token_no_se_transcribe(con_token):
    """El gateway no lo enruta, pero eso es una tabla de ruteo, no una frontera."""
    async with await _cliente() as c:
        resp = await c.post("/transcribe", **_audio())
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "invalid_internal_token"


async def test_con_token_equivocado_tampoco(con_token):
    async with await _cliente() as c:
        resp = await c.post("/transcribe", headers={"X-Internal-Token": "otro"}, **_audio())
    assert resp.status_code == 403


async def test_sin_token_CONFIGURADO_el_servicio_no_atiende(monkeypatch):
    """Fail-closed: un servicio que escucha sin saber quién debe llamarle está abierto.

    La alternativa —atender cuando no hay token configurado— es la que dejó este
    endpoint accesible desde toda la red de contenedores.
    """
    monkeypatch.setattr(settings, "internal_service_token", "")
    async with await _cliente() as c:
        resp = await c.post("/transcribe", headers={"X-Internal-Token": "loquesea"}, **_audio())
    assert resp.status_code == 401
    assert resp.json()["detail"]["error"] == "internal_token_no_configurado"


async def test_un_audio_enorme_se_rechaza_antes_de_leerlo(con_token, monkeypatch):
    """Este proceso carga además un modelo: es el que menos memoria libre tiene."""
    monkeypatch.setattr(settings, "max_audio_bytes", 1024)
    grande = b"\x00" * 4096

    async with await _cliente() as c:
        resp = await c.post(
            "/transcribe",
            headers={"X-Internal-Token": _TOKEN},
            files={"file": ("dictado.webm", grande, "audio/webm")},
        )

    assert resp.status_code == 413
    assert resp.json()["detail"]["error"] == "audio_demasiado_grande"


async def test_el_dictado_normal_pasa_el_gate(con_token):
    """El tope no puede dejar inservible el dictado que protege.

    Con el token correcto y un audio pequeño, la petición ATRAVIESA el gate y el
    tope; muere después, en el 503 de «modelo no cargado», que es lo esperable
    sin modelo en un contenedor de pruebas. Comprobarlo es lo que distingue «el
    gate deja pasar» de «el gate rechaza todo».
    """
    async with await _cliente() as c:
        resp = await c.post("/transcribe", headers={"X-Internal-Token": _TOKEN}, **_audio())

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "modelo_no_cargado"
