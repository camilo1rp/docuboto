# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`GET /api/v1/trd/disposicion-vencida` — la cola de vencimientos (RF-TRD-08).

Es la parte de la disposición final **que no destruye nada**: el sistema deja de
limitarse a guardar el calendario congelado al cerrar y pasa a decir en voz alta
qué venció, cuándo, bajo qué acto y qué toca hacer con ello.

Tres cosas se prueban aquí porque son las que fallan en silencio:

1. **El recuento va bajo la MISMA condición que las filas.** Recortar solo las
   filas dejaría que el total delatara cuántos expedientes clasificados hay
   vencidos — la lección de la cola de anulaciones, donde el recuento era el
   punto delicado y no la página.
2. **Un vencimiento no es una orden de destruir.** Sobre `CT` el vencimiento del
   archivo central significa conservar para siempre en el histórico. Si la cola
   no lo distinguiera, se leería como «lista de lo que hay que borrar»
   incluyendo justo los expedientes que jamás se borran.
3. **`gobierna_eliminacion` no reimplementa el gate**: sale del mismo predicado
   que `assert_disposicion_final_autorizada` usará cuando llegue el brazo
   ejecutor. Si divergieran, la pantalla diría «se puede eliminar» sobre un
   expediente que el gate va a rechazar.
"""

import json
import uuid
from datetime import UTC, date, datetime

import pytest

from tests.conftest import TENANT_HEADERS

RUTA = "/api/v1/trd/disposicion-vencida"


def _fila(
    *,
    code: str = "EXP-2026-0001",
    nivel: int = 1,
    disposition_code: str = "E",
    serie_estado: str = "convalidada",
    acto: str | None = "Resolución 214 de 2026",
    dias: int = 30,
    decision: str | None = None,
    decision_motivo: str | None = None,
    revisar_de_nuevo_el: date | None = None,
) -> dict:
    snapshot = {
        "disposition_code": disposition_code,
        "disposition_label": "Eliminación" if disposition_code == "E" else "Conservación Total",
        "serie_estado": serie_estado,
        "acto_administrativo": acto,
        "fin_archivo_central": "2026-07-29",
        "fin_archivo_gestion": "2024-07-29",
    }
    return {
        "id": uuid.uuid4(),
        "code": code,
        "status": "closed",
        "nivel_seguridad": nivel,
        "trd_serie_code": "DEM-100",
        "closed_at": datetime(2026, 1, 1, tzinfo=UTC),
        "disposition": json.dumps(snapshot),
        "fecha_vencimiento": date(2026, 7, 29),
        "dias_vencido": dias,
        # Estado de la decisión sobre ESTE vencimiento (RF-TRD-06): `None` es
        # «nadie lo ha mirado todavía», que es el caso por defecto de la cola.
        "decision": decision,
        "decision_motivo": decision_motivo,
        "revisar_de_nuevo_el": revisar_de_nuevo_el,
        "decidido_por": None,
        "decidido_at": None,
    }


def _sql_emitido(mock_conn) -> tuple[str, str]:
    """(SQL del COUNT, SQL de la página). Se inspecciona lo ENVIADO, no el
    fuente: un `getsource` encuentra la frase en el docstring que la niega."""
    count = mock_conn.fetchval.await_args_list[-1].args[0]
    pagina = mock_conn.fetch.await_args_list[-1].args[0]
    return count, pagina


@pytest.mark.asyncio
async def test_el_recuento_recorta_por_clearance_igual_que_las_filas(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # permiso concedido / total
    mock_conn.fetch.return_value = []

    resp = await client.get(RUTA, headers=TENANT_HEADERS)
    assert resp.status_code == 200

    count_sql, pagina_sql = _sql_emitido(mock_conn)
    assert "nivel_seguridad <= $1" in count_sql, count_sql
    assert "nivel_seguridad <= $1" in pagina_sql, pagina_sql
    # Y es LA MISMA condición, no dos parecidas: todo lo que filtra la página
    # filtra el total.
    for cond in ("e.disposition IS NOT NULL", "<= CURRENT_DATE", "status IN"):
        assert cond in count_sql, (cond, count_sql)
        assert cond in pagina_sql, (cond, pagina_sql)


@pytest.mark.asyncio
async def test_conservacion_total_no_se_presenta_como_eliminable(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = [_fila(disposition_code="CT")]

    resp = await client.get(RUTA, headers=TENANT_HEADERS)

    assert resp.status_code == 200
    fila = resp.json()[0]
    assert fila["disposition_code"] == "CT"
    assert "conservar permanentemente" in fila["accion"]
    assert "no se elimina" in fila["accion"]


@pytest.mark.asyncio
async def test_eliminacion_dice_que_exige_acta_y_aval(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = [_fila(disposition_code="E")]

    resp = await client.get(RUTA, headers=TENANT_HEADERS)

    fila = resp.json()[0]
    assert "acta" in fila["accion"]
    assert "Comité" in fila["accion"]
    assert fila["gobierna_eliminacion"] is True


@pytest.mark.asyncio
async def test_una_serie_migrada_no_gobierna_la_eliminacion(client_with_conn) -> None:
    """Control negativo del predicado compartido: `migrada` gobierna un CIERRE
    (ADR-025 D6) pero NO una eliminación efectiva (D5). La cola debe decir lo
    mismo que dirá el gate."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = [_fila(serie_estado="migrada", acto=None)]

    resp = await client.get(RUTA, headers=TENANT_HEADERS)

    assert resp.json()[0]["gobierna_eliminacion"] is False


@pytest.mark.asyncio
async def test_derogada_con_acto_si_gobierna(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = [_fila(serie_estado="derogada", acto="Res. 9 de 2025")]

    assert (await client.get(RUTA, headers=TENANT_HEADERS)).json()[0][
        "gobierna_eliminacion"
    ] is True


@pytest.mark.asyncio
async def test_derogada_sin_acto_no_gobierna(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = [_fila(serie_estado="derogada", acto=None)]

    assert (await client.get(RUTA, headers=TENANT_HEADERS)).json()[0][
        "gobierna_eliminacion"
    ] is False


@pytest.mark.asyncio
async def test_la_fase_elige_la_fecha_y_la_accion(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    # `fetchval` sirve al gate Y al COUNT: un 0 aquí denegaría el permiso.
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = []

    await client.get(f"{RUTA}?fase=gestion", headers=TENANT_HEADERS)
    _, pagina_sql = _sql_emitido(mock_conn)
    assert "fin_archivo_gestion" in pagina_sql
    assert "fin_archivo_central" not in pagina_sql

    await client.get(f"{RUTA}?fase=central", headers=TENANT_HEADERS)
    _, pagina_sql = _sql_emitido(mock_conn)
    assert "fin_archivo_central" in pagina_sql


@pytest.mark.asyncio
async def test_fase_desconocida_es_422_no_una_consulta_rara(client_with_conn) -> None:
    """La fase se interpola en el SQL, así que el `Literal` del router no es
    cosmético: es la primera de las dos barreras (la segunda es el mapa cerrado
    del repositorio)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 1  # con permiso: lo que se prueba es la validación
    resp = await client.get(f"{RUTA}?fase='; DROP TABLE expedientes--", headers=TENANT_HEADERS)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_un_snapshot_ilegible_no_tumba_el_informe(client_with_conn) -> None:
    """Una fila con `disposition` corrupta sale sin afirmar nada sobre su
    eliminación, en vez de reventar el listado entero — el resto de la cola
    sigue siendo trabajo pendiente de alguien."""
    client, mock_conn = client_with_conn
    rota = _fila(code="EXP-ROTA")
    rota["disposition"] = "{no es json"
    mock_conn.fetchval.return_value = 2
    mock_conn.fetch.return_value = [rota, _fila(code="EXP-BUENA")]

    resp = await client.get(RUTA, headers=TENANT_HEADERS)

    assert resp.status_code == 200
    cuerpo = {f["code"]: f for f in resp.json()}
    assert cuerpo["EXP-ROTA"]["gobierna_eliminacion"] is False
    assert cuerpo["EXP-ROTA"]["disposition_code"] is None
    assert cuerpo["EXP-BUENA"]["gobierna_eliminacion"] is True


@pytest.mark.asyncio
async def test_sin_permiso_no_se_enumera_nada(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False

    resp = await client.get(RUTA, headers=TENANT_HEADERS)

    assert resp.status_code == 403
    mock_conn.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_deja_traza_de_quien_consulto_la_cola(client_with_conn) -> None:
    """Enumerar los expedientes que están a un paso de eliminarse es
    exactamente la clase de consulta que debe dejar rastro (RF-BUS-10), igual
    que su gemelo el informe de revisión pendiente."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = 1
    mock_conn.fetch.return_value = [_fila()]

    await client.get(RUTA, headers={**TENANT_HEADERS, "X-Canal": "api"})

    asientos = [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str)
        and "INSERT INTO public.audit_log" in c.args[0]
        and c.args[5] == "archive.expediente_disposicion_vencida_listado"
    ]
    assert asientos, "la consulta de la cola de vencimientos no dejó asiento"
