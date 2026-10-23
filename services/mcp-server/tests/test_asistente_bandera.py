# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""ADR-037: las cinco rutas de `/api/v1/assistant/*` exigen la bandera de la entidad y
`PERM_CONOCIMIENTO` nivel 1, y niegan ANTES de tocar el SDK de Anthropic o Whisper.

Cada negativo tiene su control positivo (la misma ruta pasa con ambas condiciones).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.services.stt import SttProvider, Transcripcion
from tests._assistant_fakes import FakeAnthropic, Response, make_gateway, text_block

_AUTH = {"Authorization": "Bearer tok", "X-Tenant-Slug": "demo"}


class _EspiaStt(SttProvider):
    nombre = "whisper"

    def __init__(self) -> None:
        self.llamadas = 0

    async def transcribir(self, audio, *, nombre_fichero, mime, idioma):  # type: ignore[no-untyped-def]
        self.llamadas += 1
        return Transcripcion(texto="hola", idioma=idioma, proveedor="whisper", modelo="small")


def _montar(**gw: Any) -> tuple[FakeAnthropic, _EspiaStt]:
    anthropic = FakeAnthropic([Response([text_block("Hola.")], "end_turn")])
    stt = _EspiaStt()
    app.state.gateway_client = make_gateway(**gw)
    app.state.anthropic_client = anthropic
    app.state.assistant_available = True
    app.state.conversations = {}
    app.state.stt_provider = stt
    return anthropic, stt


@pytest_asyncio.fixture
async def api() -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac


# (nombre, método, ruta, kwargs de httpx)
RUTAS = [
    ("message", "POST", "/api/v1/assistant/message", {"json": {"mensaje": "hola"}}),
    ("alcance", "GET", "/api/v1/assistant/alcance", {}),
    ("confirmar", "POST", "/api/v1/assistant/confirmar", {"json": {"token": "x.y.z"}}),
    ("transcribe_get", "GET", "/api/v1/assistant/transcribe", {}),
    (
        "transcribe_post",
        "POST",
        "/api/v1/assistant/transcribe",
        {"files": {"file": ("d.webm", b"audio", "audio/webm")}},
    ),
]
IDS = [r[0] for r in RUTAS]


async def _llamar(api: AsyncClient, ruta: tuple) -> Any:
    _, metodo, url, kw = ruta
    return await api.request(metodo, url, headers=_AUTH, **kw)


@pytest.mark.asyncio
@pytest.mark.parametrize("ruta", RUTAS, ids=IDS)
async def test_con_la_entidad_apagada_403_y_no_se_toca_ningun_proveedor(api, ruta) -> None:
    anthropic, stt = _montar(permissions=["PERM_CONOCIMIENTO"], asistente_habilitado=False)

    resp = await _llamar(api, ruta)

    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["error"] == "asistente_deshabilitado"
    assert anthropic.messages.calls == [], "se llamó a Anthropic con el asistente apagado"
    assert stt.llamadas == 0, "se transcribió con el asistente apagado"


@pytest.mark.asyncio
@pytest.mark.parametrize("ruta", RUTAS, ids=IDS)
async def test_la_entidad_apagada_tampoco_la_salva_ser_root(api, ruta) -> None:
    anthropic, stt = _montar(root=True, asistente_habilitado=False)

    resp = await _llamar(api, ruta)

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "asistente_deshabilitado"
    assert anthropic.messages.calls == [] and stt.llamadas == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("ruta", RUTAS, ids=IDS)
async def test_sin_el_permiso_403_forbidden_aunque_la_entidad_lo_active(api, ruta) -> None:
    anthropic, stt = _montar(permissions=[], con_conocimiento=False, asistente_habilitado=True)

    resp = await _llamar(api, ruta)

    assert resp.status_code == 403, resp.text
    assert resp.json()["detail"]["error"] == "forbidden"
    assert "PERM_CONOCIMIENTO" in resp.json()["detail"]["detail"]
    assert anthropic.messages.calls == [] and stt.llamadas == 0


@pytest.mark.asyncio
async def test_el_permiso_en_nivel_0_no_basta(api) -> None:
    _montar(permissions=["PERM_CONOCIMIENTO"], levels={"PERM_CONOCIMIENTO": 0})
    resp = await _llamar(api, RUTAS[0])
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_con_bandera_y_permiso_el_turno_corre(api) -> None:
    """Control positivo de /message: el mismo cuerpo, ambas condiciones, 200."""
    anthropic, _ = _montar(permissions=["PERM_CONOCIMIENTO"], asistente_habilitado=True)

    resp = await _llamar(api, RUTAS[0])

    assert resp.status_code == 200, resp.text
    assert len(anthropic.messages.calls) == 1


@pytest.mark.asyncio
async def test_con_bandera_y_permiso_alcance_y_transcribe_responden(api) -> None:
    """Control positivo del resto: ninguna ruta queda cerrada por el gate cuando se cumple."""
    _, stt = _montar(permissions=["PERM_CONOCIMIENTO"], asistente_habilitado=True)

    assert (await _llamar(api, RUTAS[1])).status_code == 200
    assert (await _llamar(api, RUTAS[3])).status_code == 200
    post = await _llamar(api, RUTAS[4])
    assert post.status_code == 200
    assert stt.llamadas == 1


@pytest.mark.asyncio
async def test_confirmar_con_ambas_condiciones_llega_al_token(api) -> None:
    """Pasa el gate y recién ahí falla por el token inválido (no por 403)."""
    _montar(permissions=["PERM_CONOCIMIENTO"], asistente_habilitado=True)
    resp = await _llamar(api, RUTAS[2])
    assert resp.status_code != 403
    assert resp.json()["detail"]["error"] != "asistente_deshabilitado"


@pytest.mark.asyncio
@pytest.mark.parametrize("ruta", RUTAS[3:], ids=IDS[3:])
async def test_transcribe_ya_no_es_anonimo(api, ruta) -> None:
    _montar(permissions=["PERM_CONOCIMIENTO"])
    _, metodo, url, kw = ruta
    resp = await api.request(metodo, url, **kw)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_una_bandera_que_no_es_true_literal_no_enciende(api) -> None:
    """`"true"`, `1` o cualquier otra cosa en /auth/me NO cuenta como encendido."""
    for valor in ("true", 1, "si", {"x": 1}):
        gw = make_gateway(permissions=["PERM_CONOCIMIENTO"])
        original = gw.get.side_effect

        async def _get(path: str, _o=original, _v=valor, **kw: Any):  # type: ignore[no-untyped-def]
            resp = await _o(path, **kw)
            if path == "/api/v1/auth/me":
                import httpx

                cuerpo = resp.json() | {"asistente_habilitado": _v}
                return httpx.Response(200, json=cuerpo)
            return resp

        gw.get.side_effect = _get
        app.state.gateway_client = gw
        app.state.anthropic_client = FakeAnthropic([])
        app.state.assistant_available = True
        app.state.conversations = {}
        resp = await _llamar(api, RUTAS[0])
        assert resp.status_code == 403, valor


@pytest.mark.asyncio
async def test_root_real_es_USUA_PERM_ROOT_y_pasa_sin_fila_de_conocimiento(api) -> None:
    """S2: ROOT llega como `permission_levels.USUA_PERM_ROOT = 5` y no necesita PERM_CONOCIMIENTO."""
    anthropic, _ = _montar(root=True, con_conocimiento=False, asistente_habilitado=True)
    resp = await _llamar(api, RUTAS[0])
    assert resp.status_code == 200, resp.text
    assert len(anthropic.messages.calls) == 1


@pytest.mark.asyncio
async def test_un_rol_ROOT_en_el_jwt_no_hace_root(api) -> None:
    """S2: el realm no emite el rol «ROOT»; si apareciera no concede nada por sí solo."""
    anthropic, _ = _montar(roles=["ROOT"], con_conocimiento=False, asistente_habilitado=True)
    resp = await _llamar(api, RUTAS[0])
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    assert anthropic.messages.calls == []
