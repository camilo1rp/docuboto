# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El lote de eliminación — RF-TRD-07, fase 1 de 5.

Se elimina POR LOTES porque el acta y el aval del Comité son del lote. Esta fase
propone y **congela el inventario**; publicar, avalar, firmar y ejecutar el
borrado seguro son las cuatro siguientes, y la última no está construida.

Se prueban **las cinco condiciones de admisión, una por una**, porque cada una
protege algo distinto y un test que solo mirara «entra / no entra» no
distinguiría cuál falló:

1. Sin confirmación no hay propuesta — es la única barrera contra la
   destrucción automática.
2. Conservación Total **jamás** entra.
3. El instrumento congelado tiene que gobernar la eliminación (ADR-025 D5).
4. El candado WORM tiene que haber expirado — y es el **máximo de los tres**
   que puede tener un expediente.
5. No puede estar ya en otro lote vivo.

Y se prueba lo que hace utilizable el endpoint: **lo rechazado se informa**.
Quien propone 40 y recibe 31 tiene que ver cuáles nueve faltan y por qué.
"""

import json
import uuid
from datetime import UTC, date, datetime, timedelta

import pytest

from tests.conftest import TENANT_HEADERS

RUTA = "/api/v1/eliminacion/lotes"
_CLEARANCE = {"is_root": False, "max_level": 3}


def _candidato(
    *,
    exp_id: uuid.UUID | None = None,
    code: str = "EXP-2020-0001",
    decision: str = "confirmada",
    disposition_code: str = "E",
    serie_estado: str = "convalidada",
    acto: str | None = "Res. 214 de 2026",
    worm: datetime | None = None,
    lote_existente: str | None = None,
    nivel: int = 1,
) -> dict:
    return {
        "id": exp_id or uuid.uuid4(),
        "code": code,
        "name": "Contratos 2020",
        "status": "closed",
        "nivel_seguridad": nivel,
        "trd_serie_code": "DEM-100",
        "disposition": json.dumps(
            {
                "disposition_code": disposition_code,
                "disposition_label": "Eliminación" if disposition_code == "E" else "Conservación Total",
                "serie_estado": serie_estado,
                "acto_administrativo": acto,
            }
        ),
        "fecha_extrema_inicial": date(2020, 1, 1),
        "fecha_extrema_final": date(2020, 12, 31),
        "fecha_vencimiento": date(2026, 1, 1),
        "decision": decision,
        "radicados_incluidos": 12,
        # `-infinity` es lo que devuelve GREATEST sobre tres COALESCE cuando no
        # hay ningún candado: se usa el valor REAL de Postgres, no None, para
        # que el test ejerza la traducción.
        "worm_retain_until": worm or datetime.min.replace(tzinfo=UTC),
        "lote_existente": lote_existente,
    }


def _lote_row(codigo="LOTE-2026-001") -> dict:
    return {
        "id": uuid.uuid4(),
        "codigo": codigo,
        "estado": "borrador",
        "observaciones": None,
        "creado_por": uuid.uuid4(),
        "creado_at": datetime(2026, 8, 28, tzinfo=UTC),
        "publicado_at": None,
        "publicado_hasta": None,
        "acta_comite": None,
        "fecha_aval": None,
        "acto_administrativo": None,
        "ejecutado_at": None,
        "anulado_motivo": None,
    }


async def _proponer(client, mock_conn, candidatos, ids=None):
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [_CLEARANCE, _lote_row()]
    mock_conn.fetch.return_value = candidatos
    return await client.post(
        RUTA,
        json={
            "codigo": "LOTE-2026-001",
            "expediente_ids": [str(c["id"]) for c in candidatos] if ids is None else ids,
        },
        headers=TENANT_HEADERS,
    )


# ── Las cinco condiciones ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_un_expediente_confirmado_y_vencido_entra(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    resp = await _proponer(client, mock_conn, [_candidato()])

    assert resp.status_code == 201
    cuerpo = resp.json()
    assert cuerpo["total_items"] == 1
    assert cuerpo["rechazados"] == []
    assert cuerpo["ejecutado"] is False
    assert "NADA se ha eliminado" in cuerpo["nota"]
    # Sin candado, el `-infinity` de Postgres NO se pinta como fecha.
    assert cuerpo["items"][0]["worm_retain_until"] is None


@pytest.mark.asyncio
async def test_1_sin_confirmacion_no_hay_propuesta(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    resp = await _proponer(client, mock_conn, [_candidato(decision=None)])

    assert resp.status_code == 409
    rechazos = resp.json()["detail"]["rechazados"]
    assert rechazos[0]["motivo"] == "sin_confirmacion"
    assert "automática" in rechazos[0]["detalle"]


@pytest.mark.asyncio
async def test_2_conservacion_total_jamas_entra(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    resp = await _proponer(client, mock_conn, [_candidato(disposition_code="CT")])

    assert resp.status_code == 409
    r = resp.json()["detail"]["rechazados"][0]
    assert r["motivo"] == "disposicion_no_destruye"
    assert "conserva permanentemente" in r["detalle"]


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["E", "S", "M"])
async def test_2bis_las_tres_que_destruyen_si_entran(client_with_conn, code: str) -> None:
    """`S` elimina lo no seleccionado y `M` elimina el original: las tres
    terminan en destrucción y las tres son elegibles."""
    client, mock_conn = client_with_conn
    resp = await _proponer(client, mock_conn, [_candidato(disposition_code=code)])
    assert resp.status_code == 201, code


@pytest.mark.asyncio
async def test_3_un_instrumento_que_no_gobierna_no_entra(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    resp = await _proponer(
        client, mock_conn, [_candidato(serie_estado="migrada", acto=None)]
    )

    assert resp.status_code == 409
    r = resp.json()["detail"]["rechazados"][0]
    assert r["motivo"] == "instrumento_no_gobierna"


@pytest.mark.asyncio
async def test_4_un_candado_worm_vigente_no_entra_y_dice_hasta_cuando(
    client_with_conn,
) -> None:
    """En modo COMPLIANCE nadie puede borrar antes de `retain_until`, ni el
    administrador. Aceptarlo daría un lote imposible de ejecutar — y el
    procedimiento se enteraría al final, con el Comité ya movilizado."""
    client, mock_conn = client_with_conn
    futuro = datetime.now(tz=UTC) + timedelta(days=400)
    resp = await _proponer(client, mock_conn, [_candidato(worm=futuro)])

    assert resp.status_code == 409
    r = resp.json()["detail"]["rechazados"][0]
    assert r["motivo"] == "worm_vigente"
    assert futuro.date().isoformat() in r["detalle"]


@pytest.mark.asyncio
async def test_4bis_un_candado_ya_expirado_si_entra(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    pasado = datetime.now(tz=UTC) - timedelta(days=10)
    resp = await _proponer(client, mock_conn, [_candidato(worm=pasado)])

    assert resp.status_code == 201
    assert resp.json()["items"][0]["worm_retain_until"] is not None


@pytest.mark.asyncio
async def test_5_no_puede_estar_en_dos_lotes(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    resp = await _proponer(
        client, mock_conn, [_candidato(lote_existente="LOTE-2026-000")]
    )

    assert resp.status_code == 409
    r = resp.json()["detail"]["rechazados"][0]
    assert r["motivo"] == "ya_en_otro_lote"
    assert "LOTE-2026-000" in r["detalle"]


# ── El informe, que es lo que hace utilizable el endpoint ────────────────────


@pytest.mark.asyncio
async def test_lo_rechazado_se_informa_junto_a_lo_admitido(client_with_conn) -> None:
    """Quien propone tres y recibe uno tiene que ver los otros dos y qué les
    pasa. Descartar en silencio dejaría como única salida probar de uno en uno."""
    client, mock_conn = client_with_conn
    bueno = _candidato(code="EXP-BUENO")
    sin_decidir = _candidato(code="EXP-SIN-DECIDIR", decision=None)
    conservacion = _candidato(code="EXP-CT", disposition_code="CT")

    resp = await _proponer(client, mock_conn, [bueno, sin_decidir, conservacion])

    assert resp.status_code == 201
    cuerpo = resp.json()
    assert cuerpo["total_items"] == 1
    assert [i["expediente_code"] for i in cuerpo["items"]] == ["EXP-BUENO"]
    motivos = {r["expediente_code"]: r["motivo"] for r in cuerpo["rechazados"]}
    assert motivos == {
        "EXP-SIN-DECIDIR": "sin_confirmacion",
        "EXP-CT": "disposicion_no_destruye",
    }


@pytest.mark.asyncio
async def test_un_expediente_fuera_de_alcance_no_se_distingue_de_uno_inexistente(
    client_with_conn,
) -> None:
    """Distinguirlos convertiría el endpoint en un oráculo de qué expedientes
    clasificados existen — el mismo criterio que el 404 neutro de la bandeja de
    correo y del acceso directo por número."""
    client, mock_conn = client_with_conn
    fantasma = str(uuid.uuid4())
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [_CLEARANCE, _lote_row()]
    mock_conn.fetch.return_value = []  # el WHERE por clearance no lo devolvió

    resp = await client.post(
        RUTA,
        json={"codigo": "LOTE-2026-001", "expediente_ids": [fantasma]},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 409
    r = resp.json()["detail"]["rechazados"][0]
    assert r["motivo"] == "no_encontrado"
    assert "no está a su alcance" in r["detalle"]


@pytest.mark.asyncio
async def test_el_inventario_se_congela_con_lo_que_habia(client_with_conn) -> None:
    """No es una copia por comodidad: es lo que se publica para que un tercero
    pueda oponerse, y el expediente va a dejar de existir."""
    client, mock_conn = client_with_conn
    await _proponer(client, mock_conn, [_candidato(code="EXP-2020-0007")])

    insert = next(
        c
        for c in mock_conn.execute.await_args_list
        if isinstance(c.args[0], str) and "INSERT INTO eliminacion_lote_items" in c.args[0]
    )
    assert "EXP-2020-0007" in insert.args
    assert date(2020, 1, 1) in insert.args  # fecha extrema inicial
    assert 12 in insert.args  # radicados incluidos


@pytest.mark.asyncio
async def test_proponer_deja_asiento_inmutable(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    await _proponer(client, mock_conn, [_candidato()])

    asientos = [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str)
        and "INSERT INTO public.audit_log" in c.args[0]
        and c.args[5] == "archive.eliminacion_lote_propuesto"
    ]
    assert asientos


@pytest.mark.asyncio
async def test_sin_nivel_crud_no_se_propone_nada(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False

    resp = await client.post(
        RUTA,
        json={"codigo": "LOTE-2026-001", "expediente_ids": [str(uuid.uuid4())]},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 403
    mock_conn.fetch.assert_not_called()
