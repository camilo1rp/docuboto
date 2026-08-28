# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Dónde pasa la frontera entre el instrumento y su circuito (2026-08-28).

Las lecturas de TRD y TVD no son una sola cosa, y tratarlas igual falla en los
dos sentidos:

* El **vocabulario** —listado, detalle y cálculo de retención— lo consumen
  `/radicar` y `/expedientes` para poder clasificar. Exigir `USUA_PERM_TRD`
  aquí dejaría sin radicar a todo operador ordinario: ese permiso lo tiene
  quien ADMINISTRA el instrumento, no quien lo usa. Queda abierto a cualquier
  usuario autenticado del tenant, **por decisión declarada**, y con test —si
  alguien lo «asegura» de más, esto se pone en rojo antes que la ventanilla.
* El **circuito** —`GET /{code}/versiones`— devuelve el historial completo con
  los borradores, el `acta_comite`, la `fecha_aprobacion_comite`, la
  `instancia_convalidante` y el `created_by`. Eso es la deliberación del Comité
  Institucional de Gestión y Desempeño, solo lo consume `/admin/trd` y
  `/admin/tvd`, y exige `USUA_PERM_TRD` —el mismo permiso que su gemelo
  `GET /revision-pendiente` ya exigía: era el hueco que quedaba de ese par.
"""

import uuid

import pytest

from tests.conftest import TENANT_HEADERS, make_trd_serie_record

_SOLO_TENANT = {"X-Tenant-Slug": TENANT_HEADERS["X-Tenant-Slug"]}


# ── El circuito exige el permiso ──────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("instrumento", ["trd", "tvd"])
async def test_el_historial_de_versiones_exige_permiso(client_with_conn, instrumento: str) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False  # permiso denegado

    resp = await client.get(f"/api/v1/{instrumento}/100/versiones", headers=TENANT_HEADERS)

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    # Cortó antes de la lógica: no se leyó el historial.
    mock_conn.fetch.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("instrumento", ["trd", "tvd"])
async def test_el_historial_de_versiones_falla_cerrado_sin_identidad(
    client_with_conn, instrumento: str
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # aunque "hubiera" permiso

    resp = await client.get(f"/api/v1/{instrumento}/100/versiones", headers=_SOLO_TENANT)

    assert resp.status_code == 403


@pytest.mark.asyncio
@pytest.mark.parametrize("instrumento", ["trd", "tvd"])
async def test_con_permiso_el_historial_se_sirve(client_with_conn, instrumento: str) -> None:
    """Control positivo: el 403 de arriba es del gate, no de que la ruta esté
    rota o el código no exista."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    tipo = "TRD" if instrumento == "trd" else "TVD"
    mock_conn.fetch.return_value = [
        make_trd_serie_record(code="100", name="Contratos", tipo_instrumento=tipo)
    ]

    resp = await client.get(f"/api/v1/{instrumento}/100/versiones", headers=TENANT_HEADERS)

    assert resp.status_code == 200
    assert [s["code"] for s in resp.json()] == ["100"]


# ── El vocabulario queda abierto, y eso también se prueba ─────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("instrumento", ["trd", "tvd"])
async def test_el_listado_sigue_abierto_a_quien_solo_clasifica(
    client_with_conn, instrumento: str
) -> None:
    """Sin `USUA_PERM_TRD` —el caso del radicador ordinario— el listado
    responde. Es la mitad de la decisión que un endurecimiento posterior
    rompería en silencio: la ventanilla se quedaría sin series que ofrecer y
    el síntoma sería «no puedo radicar», lejos de su causa."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False  # NO tiene el permiso de administrar
    tipo = "TRD" if instrumento == "trd" else "TVD"
    mock_conn.fetch.return_value = [
        make_trd_serie_record(code="100", name="Contratos", tipo_instrumento=tipo)
    ]

    resp = await client.get(f"/api/v1/{instrumento}?is_active=true", headers=TENANT_HEADERS)

    assert resp.status_code == 200
    assert [s["code"] for s in resp.json()] == ["100"]


@pytest.mark.asyncio
@pytest.mark.parametrize("instrumento", ["trd", "tvd"])
async def test_el_detalle_y_la_retencion_siguen_abiertos(
    client_with_conn, instrumento: str
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False
    tipo = "TRD" if instrumento == "trd" else "TVD"
    serie_id = uuid.uuid4()
    mock_conn.fetchrow.return_value = make_trd_serie_record(
        code="100", name="Contratos", tipo_instrumento=tipo
    )

    detalle = await client.get(f"/api/v1/{instrumento}/{serie_id}", headers=TENANT_HEADERS)
    # El nombre del parámetro de fecha base difiere por instrumento (ADR-026 D4):
    # la TRD cuenta desde el cierre del expediente, la TVD desde la fecha extrema
    # final del fondo acumulado.
    fecha = "closed_at" if instrumento == "trd" else "base_date"
    retencion = await client.get(
        f"/api/v1/{instrumento}/{serie_id}/retention?{fecha}=2026-01-31",
        headers=TENANT_HEADERS,
    )

    assert detalle.status_code == 200
    assert retencion.status_code == 200
