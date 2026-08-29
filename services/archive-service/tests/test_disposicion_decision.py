# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`POST /trd/disposicion-vencida/{id}/decidir` — la confirmación (RF-TRD-06/08).

«Ninguna destrucción es automática sin confirmación de usuario autorizado»
(RT 04, regla de negocio 4). Esta es esa confirmación, y **sigue sin destruir
nada**: es la precondición del brazo ejecutor, no su disparo.

Lo que se prueba es lo que falla en silencio:

* **El gate de ADR-025 D5 recibe por fin un llamante.** Confirmar la
  eliminación de un expediente cuyo instrumento congelado no la gobierna se
  rechaza AQUÍ, no al final del procedimiento cuando ya se movilizó al Comité.
* **Sobre `CT` el gate no aplica**: conservación total no termina en
  destrucción, y exigir un instrumento convalidado para confirmar que algo se
  conserva sería un requisito inventado.
* **La fecha y el código NO se toman del cliente.** Salen del snapshot
  congelado; si viajaran en el cuerpo, confirmar una eliminación sería
  cuestión de mandar la fecha adecuada.
* **El asiento inmutable es el registro del acto**, en la misma transacción:
  si `audit_log` no puede recibirlo, la decisión no se toma.
* **La respuesta no deja creer que algo ocurrió** (`ejecutada: false`).
"""

import json
import uuid
from datetime import UTC, date, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import TENANT_HEADERS

EXP = uuid.uuid4()
RUTA = f"/api/v1/trd/disposicion-vencida/{EXP}/decidir"
MANANA = (date.today() + timedelta(days=90)).isoformat()


def _vencimiento(*, code: str = "E", serie_estado: str = "convalidada", acto="Res. 1") -> dict:
    return {
        "id": EXP,
        "code": "EXP-2026-0031",
        "status": "closed",
        "nivel_seguridad": 1,
        "disposition": json.dumps(
            {
                "disposition_code": code,
                "serie_estado": serie_estado,
                "acto_administrativo": acto,
                "code": "DEM-100",
                "serie_version": 2,
            }
        ),
        "fecha_vencimiento": date(2026, 7, 29),
    }


# `resolve_caller_clearance` consulta ANTES que nada (RF-SEG-08): su fila es
# siempre el primer `fetchrow`. CLASIFICADA para que el clearance no sea lo que
# recorta en estos tests -- lo que se prueba es la decisión, no el no-read-up
# (que tiene su propio caso, el del 404).
_CLEARANCE = {"is_root": False, "max_level": 3}


def _decision_row(**kw) -> dict:
    base = {
        "id": uuid.uuid4(),
        "fecha_vencimiento": date(2026, 7, 29),
        "decidido_at": datetime(2026, 8, 28, tzinfo=UTC),
    }
    base.update(kw)
    return base


def _asientos(mock_conn, action: str) -> list:
    return [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str)
        and "INSERT INTO public.audit_log" in c.args[0]
        and c.args[5] == action
    ]


@pytest.mark.asyncio
async def test_confirmar_registra_y_declara_que_no_ejecuto_nada(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [_CLEARANCE, _vencimiento(), _decision_row()]

    resp = await client.post(
        RUTA, json={"fase": "central", "decision": "confirmada"}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 201
    cuerpo = resp.json()
    assert cuerpo["decision"] == "confirmada"
    assert cuerpo["ejecutada"] is False
    assert "NO ha eliminado" in cuerpo["nota"]
    assert _asientos(mock_conn, "archive.disposicion_decidida")


@pytest.mark.asyncio
async def test_no_se_confirma_una_eliminacion_que_el_instrumento_no_gobierna(
    client_with_conn,
) -> None:
    """El gate de ADR-025 D5 recibe su PRIMER llamante: una serie `migrada`
    gobierna un cierre pero no una eliminación efectiva. Rechazarlo aquí evita
    que el funcionario descubra el límite al final del procedimiento."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [_CLEARANCE, _vencimiento(serie_estado="migrada", acto=None)]

    resp = await client.post(
        RUTA, json={"fase": "central", "decision": "confirmada"}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "trd_version_no_convalidada_para_disposicion_final"


@pytest.mark.asyncio
async def test_conservacion_total_no_exige_instrumento_convalidado(client_with_conn) -> None:
    """Control del alcance del gate: `CT` no termina en destrucción, así que
    pedirle un instrumento convalidado sería un requisito inventado."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [_CLEARANCE,
        _vencimiento(code="CT", serie_estado="migrada", acto=None),
        _decision_row(),
    ]

    resp = await client.post(
        RUTA, json={"fase": "central", "decision": "confirmada"}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 201


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["E", "S", "M"])
async def test_las_tres_disposiciones_que_destruyen_pasan_por_el_gate(
    client_with_conn, code: str
) -> None:
    """`S` elimina lo no seleccionado y `M` elimina el original tras
    reproducirlo: las tres destruyen, así que las tres exigen el instrumento."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [_CLEARANCE, _vencimiento(code=code, serie_estado="borrador", acto=None)]

    resp = await client.post(
        RUTA, json={"fase": "central", "decision": "confirmada"}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 409, code


@pytest.mark.asyncio
async def test_la_fecha_del_vencimiento_sale_del_snapshot_no_del_cliente(
    client_with_conn,
) -> None:
    """Si la fecha viajara en el cuerpo, confirmar una eliminación sería
    cuestión de mandar la adecuada."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [_CLEARANCE, _vencimiento(), _decision_row()]

    await client.post(
        RUTA,
        json={
            "fase": "central",
            "decision": "confirmada",
            "fecha_vencimiento": "1999-01-01",  # ignorado
            "disposition_code": "CT",  # ignorado
        },
        headers=TENANT_HEADERS,
    )

    insert = next(
        c
        for c in mock_conn.fetchrow.await_args_list
        if isinstance(c.args[0], str) and "INSERT INTO disposicion_decisiones" in c.args[0]
    )
    assert date(2026, 7, 29) in insert.args, insert.args
    assert "E" in insert.args, insert.args
    assert date(1999, 1, 1) not in insert.args


@pytest.mark.asyncio
async def test_aplazar_exige_motivo_util(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True

    resp = await client.post(
        RUTA,
        json={"fase": "central", "decision": "aplazada", "motivo": "no", "revisar_de_nuevo_el": MANANA},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_aplazar_exige_fecha_de_regreso(client_with_conn) -> None:
    """Un aplazamiento sin fecha de regreso es un olvido con mejor nombre."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True

    resp = await client.post(
        RUTA,
        json={
            "fase": "central",
            "decision": "aplazada",
            "motivo": "pendiente de concepto jurídico de la oficina asesora",
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_aplazar_con_fecha_pasada_no_es_aplazar(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True

    resp = await client.post(
        RUTA,
        json={
            "fase": "central",
            "decision": "aplazada",
            "motivo": "pendiente de concepto jurídico de la oficina asesora",
            "revisar_de_nuevo_el": "2020-01-01",
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_aplazar_bien_se_registra(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [_CLEARANCE, _vencimiento(), _decision_row()]

    resp = await client.post(
        RUTA,
        json={
            "fase": "central",
            "decision": "aplazada",
            "motivo": "pendiente de concepto jurídico de la oficina asesora",
            "revisar_de_nuevo_el": MANANA,
        },
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 201
    assert resp.json()["decision"] == "aplazada"


@pytest.mark.asyncio
async def test_un_expediente_fuera_de_alcance_da_404_no_403(client_with_conn) -> None:
    """404 y no 403: un 403 confirmaría que el expediente existe."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [_CLEARANCE, None]

    resp = await client.post(
        RUTA, json={"fase": "central", "decision": "confirmada"}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "vencimiento_no_encontrado"


@pytest.mark.asyncio
async def test_sin_nivel_crud_suficiente_no_se_decide(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False

    resp = await client.post(
        RUTA, json={"fase": "central", "decision": "confirmada"}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 403
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_si_el_asiento_no_se_puede_escribir_la_decision_NO_se_toma(
    client_with_conn,
) -> None:
    """El asiento es el registro del acto, no un acompañamiento: van en la
    misma transacción (mismo criterio que el descarte de correo y la
    anulación)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.side_effect = [_CLEARANCE, _vencimiento(), _decision_row()]

    with (
        patch(
            "app.services.archive_service.audit.append",
            new=AsyncMock(side_effect=RuntimeError("cadena caída")),
        ),
        pytest.raises(RuntimeError),
    ):
        await client.post(
            RUTA, json={"fase": "central", "decision": "confirmada"}, headers=TENANT_HEADERS
        )

    # No hubo respuesta de éxito, y el INSERT viajaba DENTRO de la transacción
    # que la excepción revienta: con la cadena caída no queda una decisión
    # registrada que nadie pueda auditar. Con mocks el rollback lo produce
    # Postgres, no el doble — la atomicidad de verdad se prueba en el carril de
    # integración (`tests/integration/`), aquí se prueba que van juntas.
    mock_conn.transaction.assert_called()
