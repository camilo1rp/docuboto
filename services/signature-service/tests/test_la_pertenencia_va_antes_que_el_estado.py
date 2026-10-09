# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""
AREA-04-09 Media-2 — un ajeno a la cadena no aprende el ESTADO de la cadena.

Antes, `firmar`/`rechazar`/`dar_visto_bueno`/`emitir_challenge` contestaban
`409 solicitud_no_pendiente` («ya está firmado / rechazado») ANTES de comprobar
si el actor era parte de la cadena. Quien solo tenía el nivel de acceso podía
sondear cualquier solicitud y distinguir por el código de respuesta una cadena
abierta de una cerrada. Ahora la pertenencia se comprueba primero: un ajeno
recibe el MISMO 403 `no_es_firmante` sea cual sea el estado, y quien está por
debajo del nivel recibe el 404 neutro antes que ninguno de los dos.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from tests.conftest import TENANT_HEADERS
from tests.test_cadena import SOL_ID, _clear, _make_conn, _override, _sol_row

ESTADOS = ["pendiente", "firmado", "rechazado", "cancelado"]

#: (ruta, cuerpo) de cada acto que comparte el patrón estado-antes-que-pertenencia.
ACTOS = [
    ("sign", None),
    ("reject", {"motivo": "no corresponde"}),
    ("vobo", None),
    ("challenge", None),
]


async def _post(ruta: str, cuerpo: dict | None):
    headers = {**TENANT_HEADERS, "Authorization": "Bearer x"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        return await ac.post(
            f"/api/v1/signature/requests/{SOL_ID}/{ruta}", json=cuerpo, headers=headers
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("estado", ESTADOS)
@pytest.mark.parametrize(("ruta", "cuerpo"), ACTOS)
async def test_un_ajeno_recibe_el_mismo_403_sea_cual_sea_el_estado(
    estado: str, ruta: str, cuerpo: dict | None
) -> None:
    conn = _make_conn()
    # fetchval: permiso RBAC (True) y es_firmante (None -> ajeno).
    conn.fetchval.side_effect = [True, None]
    # solicitud + clearance del actor (nivel 1 >= nivel 1: la ve)
    conn.fetchrow.side_effect = [_sol_row(estado=estado), {}, {}]
    _override(conn)
    try:
        resp = await _post(ruta, cuerpo)
    finally:
        _clear()
    assert resp.status_code == 403, resp.text
    detalle = resp.json()["detail"]
    assert detalle["error"] == "no_es_firmante"
    # Nada del estado se cuela en el cuerpo del 403.
    assert estado not in str(detalle) or estado == "pendiente"
    assert "solicitud_no_pendiente" not in str(detalle)


@pytest.mark.asyncio
@pytest.mark.parametrize("estado", ["firmado", "rechazado"])
@pytest.mark.parametrize(("ruta", "cuerpo"), ACTOS)
async def test_un_firmante_de_la_cadena_si_ve_el_estado(
    estado: str, ruta: str, cuerpo: dict | None
) -> None:
    """Control positivo: el orden nuevo no tapó el 409 a quien sí es parte."""
    conn = _make_conn()
    conn.fetchval.side_effect = [True, True]  # permiso, es_firmante
    conn.fetchrow.side_effect = [_sol_row(estado=estado), {}, {}]
    _override(conn)
    try:
        resp = await _post(ruta, cuerpo)
    finally:
        _clear()
    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == "solicitud_no_pendiente"


@pytest.mark.asyncio
@pytest.mark.parametrize("estado", ESTADOS)
async def test_el_lote_no_filtra_el_estado_a_un_ajeno(estado: str) -> None:
    """`firmar_lote` delega en `firmar`: el `detalle` por ítem es el código del
    primer guard que falla, así que heredaba el defecto del orden."""
    conn = _make_conn()
    conn.fetchval.side_effect = [True, None]
    conn.fetchrow.side_effect = [_sol_row(estado=estado), {}]
    _override(conn)
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            resp = await ac.post(
                "/api/v1/signature/batch",
                json={"items": [{"solicitud_id": str(SOL_ID)}]},
                headers={**TENANT_HEADERS, "Authorization": "Bearer x"},
            )
    finally:
        _clear()
    assert resp.status_code == 200, resp.text
    [item] = resp.json()["resultados"]
    assert item["resultado"] == "omitido"
    assert item["detalle"] == "no_es_firmante"


@pytest.mark.asyncio
@pytest.mark.parametrize("estado", ESTADOS)
@pytest.mark.parametrize(("ruta", "cuerpo"), ACTOS)
async def test_bajo_el_nivel_manda_el_404_neutro_sea_cual_sea_el_estado(
    estado: str, ruta: str, cuerpo: dict | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El 404 (no-read-up) sigue yendo ANTES que la pertenencia y que el estado."""
    from app.services import cadena_service as mod

    async def _clearance_publica(conn, actor):  # noqa: ANN001
        return 1

    monkeypatch.setattr(mod, "resolve_caller_clearance", _clearance_publica)
    conn = _make_conn()
    conn.fetchval.side_effect = [True, True]
    conn.fetchrow.side_effect = [_sol_row(estado=estado, nivel_seguridad=3), {}]
    _override(conn)
    try:
        resp = await _post(ruta, cuerpo)
    finally:
        _clear()
    assert resp.status_code == 404, resp.text
