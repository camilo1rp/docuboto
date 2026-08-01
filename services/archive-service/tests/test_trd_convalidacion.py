# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Circuito de convalidación TRD (ADR-025 D2/D6, Increment 2): aprobar /
convalidar / derogar + `GET /trd/revision-pendiente`.

Ver `tests/integration/test_trd_convalidacion_atomicity.py` para la parte que
un mock no puede probar (índice único parcial, orden de derogación/promoción,
FK reales)."""

from __future__ import annotations

import uuid
from datetime import date

import pytest

from tests.conftest import TENANT_HEADERS, make_trd_serie_record


def _audit_actions(mock_conn) -> list[tuple]:
    """args de cada INSERT INTO public.audit_log, en orden de emisión."""
    return [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]


# ---------------------------------------------------------------------------
# aprobar: borrador -> aprobada
# ---------------------------------------------------------------------------


_APROBAR_BODY = {
    "acta_comite": "Acta 001 del Comité Institucional de Gestión y Desempeño",
    "fecha_aprobacion_comite": "2026-02-01",
}


@pytest.mark.asyncio
async def test_aprobar_borrador_to_aprobada(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # USUA_PERM_TRD granted
    existing = make_trd_serie_record(code="APR1", version=1, estado="borrador")
    updated = make_trd_serie_record(
        code="APR1",
        version=1,
        estado="aprobada",
        acta_comite=_APROBAR_BODY["acta_comite"],
        fecha_aprobacion_comite=date(2026, 2, 1),
    )
    mock_conn.fetchrow.side_effect = [existing, updated]

    resp = await client.post(
        "/api/v1/trd/APR1/versiones/1/aprobar", json=_APROBAR_BODY, headers=TENANT_HEADERS
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["estado"] == "aprobada"
    assert body["acta_comite"] == _APROBAR_BODY["acta_comite"]
    assert body["fecha_aprobacion_comite"] == "2026-02-01"
    actions = _audit_actions(mock_conn)
    assert len(actions) == 1
    assert actions[0][5] == "archive.trd_version_aprobada"
    assert actions[0][7] == "APR1"


@pytest.mark.asyncio
async def test_aprobar_sin_body_422(client_with_conn) -> None:
    """`acta_comite`/`fecha_aprobacion_comite` son obligatorios (migración
    038, hallazgo #5) -- sin ellos, Pydantic corta ANTES de tocar la BD."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True

    resp = await client.post("/api/v1/trd/APR1/versiones/1/aprobar", headers=TENANT_HEADERS)

    assert resp.status_code == 422
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_aprobar_version_not_found_404(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.return_value = None

    resp = await client.post(
        "/api/v1/trd/NOPE/versiones/1/aprobar", json=_APROBAR_BODY, headers=TENANT_HEADERS
    )

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "trd_version_not_found"


@pytest.mark.asyncio
@pytest.mark.parametrize("estado", ["aprobada", "convalidada", "migrada", "derogada"])
async def test_aprobar_illegal_transition_409(client_with_conn, estado: str) -> None:
    """`aprobar` solo es válido desde `borrador` (máquina de estados)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    existing = make_trd_serie_record(code="APR2", version=1, estado=estado)
    mock_conn.fetchrow.return_value = existing

    resp = await client.post(
        "/api/v1/trd/APR2/versiones/1/aprobar", json=_APROBAR_BODY, headers=TENANT_HEADERS
    )

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == "trd_transicion_invalida"
    # El gate cortó ANTES de cualquier UPDATE.
    assert mock_conn.fetchrow.call_count == 1


# ---------------------------------------------------------------------------
# devolver: aprobada -> borrador (migración 038, hallazgo #1)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_devolver_aprobada_to_borrador(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    existing = make_trd_serie_record(
        code="DEV1",
        version=1,
        estado="aprobada",
        acta_comite="Acta 001",
        fecha_aprobacion_comite=date(2026, 2, 1),
    )
    updated = make_trd_serie_record(code="DEV1", version=1, estado="borrador")
    mock_conn.fetchrow.side_effect = [existing, updated]

    resp = await client.post(
        "/api/v1/trd/DEV1/versiones/1/devolver",
        json={"motivo": "faltan ajustes de retención"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["estado"] == "borrador"
    # La aprobación queda VOID: acta/fecha se limpian en el repositorio.
    assert body["acta_comite"] is None
    actions = _audit_actions(mock_conn)
    assert len(actions) == 1
    assert actions[0][5] == "archive.trd_version_devuelta"
    import json as _json

    payload = _json.loads(actions[0][8])
    assert payload["motivo"] == "faltan ajustes de retención"
    assert payload["estado_anterior"] == "aprobada"


@pytest.mark.asyncio
async def test_devolver_sin_motivo_422(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True

    resp = await client.post(
        "/api/v1/trd/DEV1/versiones/1/devolver", json={}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 422
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize("estado", ["borrador", "convalidada", "migrada", "derogada"])
async def test_devolver_illegal_transition_409(client_with_conn, estado: str) -> None:
    """`devolver` solo es válido desde `aprobada`."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    existing = make_trd_serie_record(code="DEV2", version=1, estado=estado)
    mock_conn.fetchrow.return_value = existing

    resp = await client.post(
        "/api/v1/trd/DEV2/versiones/1/devolver",
        json={"motivo": "x"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == "trd_transicion_invalida"


# ---------------------------------------------------------------------------
# convalidar: aprobada -> convalidada (deroga la vigente previa + re-apunta FKs)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_convalidar_desde_aprobada_derogates_previa_and_repoints_fks(
    client_with_conn,
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.execute.return_value = "UPDATE 1"

    code = "CONV1"
    v1_id = uuid.uuid4()
    v2_id = uuid.uuid4()

    existing = make_trd_serie_record(
        serie_id=v2_id,
        code=code,
        version=2,
        estado="aprobada",
        acta_comite="Acta 001",
        fecha_aprobacion_comite=date(2026, 1, 15),
    )
    vigente_previa = make_trd_serie_record(
        serie_id=v1_id, code=code, version=1, estado="migrada"
    )
    derogada = make_trd_serie_record(
        serie_id=v1_id, code=code, version=1, estado="derogada", valid_to=date(2026, 3, 14)
    )
    updated = make_trd_serie_record(
        serie_id=v2_id,
        code=code,
        version=2,
        estado="convalidada",
        acto_administrativo="Resolución 0123 de 2026",
        fecha_convalidacion=date(2026, 3, 14),
        instancia_convalidante="Consejo Departamental de Archivos",
    )
    mock_conn.fetchrow.side_effect = [existing, vigente_previa, derogada, updated]

    resp = await client.post(
        f"/api/v1/trd/{code}/versiones/2/convalidar",
        json={
            "acto_administrativo": "Resolución 0123 de 2026",
            "fecha_convalidacion": "2026-03-14",
            "instancia_convalidante": "Consejo Departamental de Archivos",
            "rusd_radicado": "RUSD-2026-00987",
        },
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["estado"] == "convalidada"
    assert body["acto_administrativo"] == "Resolución 0123 de 2026"

    # Las dos FK huérfanas (aviso #3 del ADR) se re-apuntan de v1 -> v2.
    repoint_calls = [
        c.args for c in mock_conn.execute.call_args_list if "UPDATE tipos_documentales" in c.args[0]
    ]
    assert len(repoint_calls) == 1
    assert repoint_calls[0][1] == v1_id
    assert repoint_calls[0][2] == v2_id

    repoint_templates = [
        c.args
        for c in mock_conn.execute.call_args_list
        if "UPDATE expediente_metadata_templates" in c.args[0]
    ]
    assert len(repoint_templates) == 1
    assert repoint_templates[0][1] == v1_id
    assert repoint_templates[0][2] == v2_id

    # Dos asientos: convalidada (primero) y derogada (segundo, la anterior).
    actions = _audit_actions(mock_conn)
    assert [a[5] for a in actions] == [
        "archive.trd_version_convalidada",
        "archive.trd_version_derogada",
    ]
    assert actions[0][7] == code
    assert actions[1][7] == code


@pytest.mark.asyncio
async def test_convalidar_desde_aprobada_sin_vigente_previa_no_repoint(
    client_with_conn,
) -> None:
    """Caso límite: no había ninguna versión vigente previa para el código
    (p. ej. la v1 nunca llegó a convalidarse/migrar) -- no hay nada que
    derogar ni FKs que re-apuntar; un único asiento `convalidada`."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.execute.return_value = "UPDATE 0"

    code = "CONV2"
    existing = make_trd_serie_record(
        code=code,
        version=1,
        estado="aprobada",
        acta_comite="Acta 001",
        fecha_aprobacion_comite=date(2026, 1, 1),
    )
    updated = make_trd_serie_record(
        code=code,
        version=1,
        estado="convalidada",
        acto_administrativo="Resolución 1",
        fecha_convalidacion=date(2026, 1, 1),
        instancia_convalidante="Consejo Departamental",
    )
    # get_trd_serie_vigente_by_code -> None (nada vigente todavía)
    mock_conn.fetchrow.side_effect = [existing, None, updated]

    resp = await client.post(
        f"/api/v1/trd/{code}/versiones/1/convalidar",
        json={
            "acto_administrativo": "Resolución 1",
            "fecha_convalidacion": "2026-01-01",
            "instancia_convalidante": "Consejo Departamental",
        },
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200, resp.text
    repoint_calls = [
        c.args for c in mock_conn.execute.call_args_list if "UPDATE tipos_documentales" in c.args[0]
    ]
    assert repoint_calls == []
    actions = _audit_actions(mock_conn)
    assert [a[5] for a in actions] == ["archive.trd_version_convalidada"]


@pytest.mark.asyncio
async def test_convalidar_desde_aprobada_sin_acta_comite_409(client_with_conn) -> None:
    """Migración 038, hallazgo #5: convalidar desde `aprobada` exige que el
    PRIMER acto (Comité) ya conste -- sin `acta_comite`/
    `fecha_aprobacion_comite`, una fila `convalidada` sería indistinguible
    por contenido de una `migrada` ratificada."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    existing = make_trd_serie_record(code="CONVNOACT", version=1, estado="aprobada")
    mock_conn.fetchrow.return_value = existing

    resp = await client.post(
        "/api/v1/trd/CONVNOACT/versiones/1/convalidar",
        json={
            "acto_administrativo": "Resolución 1",
            "fecha_convalidacion": "2026-01-01",
            "instancia_convalidante": "Consejo Departamental",
        },
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == "trd_aprobacion_comite_requerida"
    # El gate cortó ANTES de cualquier UPDATE (solo se leyó `existing`).
    assert mock_conn.fetchrow.call_count == 1


@pytest.mark.asyncio
async def test_convalidar_desde_aprobada_carrera_huerfanos_aborta(client_with_conn) -> None:
    """Hallazgo de seguridad (huérfanos en carrera): si `vigente_previa` se
    leyó pero OTRA transacción ya la derogó/movió antes de este
    `derogar_vigente_por_code` (que devuelve `None`), la convalidación debe
    ABORTAR -- no seguir adelante dejando las FK de `tipos_documentales`/
    `expediente_metadata_templates` colgando de una fila que este acto ya no
    controla."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True

    code = "CONVRACE"
    existing = make_trd_serie_record(
        code=code,
        version=2,
        estado="aprobada",
        acta_comite="Acta 001",
        fecha_aprobacion_comite=date(2026, 1, 1),
    )
    vigente_previa = make_trd_serie_record(code=code, version=1, estado="migrada")
    # derogar_vigente_por_code -> None: otra tx ganó la carrera.
    mock_conn.fetchrow.side_effect = [existing, vigente_previa, None]

    resp = await client.post(
        f"/api/v1/trd/{code}/versiones/2/convalidar",
        json={
            "acto_administrativo": "Resolución 1",
            "fecha_convalidacion": "2026-01-01",
            "instancia_convalidante": "Consejo Departamental",
        },
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == "trd_transicion_invalida"
    # Nunca llegó a convalidar ni a re-apuntar FKs.
    assert not any(
        "UPDATE tipos_documentales" in c.args[0] for c in mock_conn.execute.call_args_list
    )
    assert _audit_actions(mock_conn) == []


@pytest.mark.asyncio
async def test_convalidar_desde_aprobada_repoint_colision_traduce_409(client_with_conn) -> None:
    """Hallazgo de seguridad (Media): si el re-apunte colisiona de todas
    formas (residual, la condición `NOT EXISTS` debería evitarlo), la
    `UniqueViolationError` cruda de Postgres se traduce a un 409 legible en
    vez de un 500."""
    import asyncpg as _asyncpg

    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True

    code = "CONVCOLL"
    v1_id = uuid.uuid4()
    v2_id = uuid.uuid4()
    existing = make_trd_serie_record(
        serie_id=v2_id,
        code=code,
        version=2,
        estado="aprobada",
        acta_comite="Acta 001",
        fecha_aprobacion_comite=date(2026, 1, 1),
    )
    vigente_previa = make_trd_serie_record(serie_id=v1_id, code=code, version=1, estado="migrada")
    derogada = make_trd_serie_record(
        serie_id=v1_id, code=code, version=1, estado="derogada", valid_to=date(2026, 3, 14)
    )
    updated = make_trd_serie_record(
        serie_id=v2_id,
        code=code,
        version=2,
        estado="convalidada",
        acto_administrativo="Resolución 1",
        fecha_convalidacion=date(2026, 1, 1),
        instancia_convalidante="Consejo Departamental",
    )
    mock_conn.fetchrow.side_effect = [existing, vigente_previa, derogada, updated]

    async def _execute_side_effect(sql, *args, **kwargs):
        if "UPDATE tipos_documentales" in sql:
            raise _asyncpg.exceptions.UniqueViolationError(
                "duplicate key value violates unique constraint"
            )
        return "UPDATE 0"

    mock_conn.execute.side_effect = _execute_side_effect

    resp = await client.post(
        f"/api/v1/trd/{code}/versiones/2/convalidar",
        json={
            "acto_administrativo": "Resolución 1",
            "fecha_convalidacion": "2026-01-01",
            "instancia_convalidante": "Consejo Departamental",
        },
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == "trd_repoint_colision"


# ---------------------------------------------------------------------------
# convalidar: migrada -> convalidada (ratificación D6, SIN versión nueva)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_convalidar_desde_migrada_ratificacion_no_new_row(client_with_conn) -> None:
    """D6: la ratificación es la MISMA fila -- ni INSERT INTO trd_series ni
    derogación de nada."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.execute.return_value = "UPDATE 3"  # 3 expedientes ratificados

    code = "MIGR1"
    serie_id = uuid.uuid4()
    existing = make_trd_serie_record(serie_id=serie_id, code=code, version=1, estado="migrada")
    updated = make_trd_serie_record(
        serie_id=serie_id,
        code=code,
        version=1,
        estado="convalidada",
        acto_administrativo="Resolución 099",
        fecha_convalidacion=date(2026, 2, 1),
        instancia_convalidante="Consejo Departamental",
    )
    mock_conn.fetchrow.side_effect = [existing, updated]

    resp = await client.post(
        f"/api/v1/trd/{code}/versiones/1/convalidar",
        json={
            "acto_administrativo": "Resolución 099",
            "fecha_convalidacion": "2026-02-01",
            "instancia_convalidante": "Consejo Departamental",
        },
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "convalidada"

    # Exactamente 2 fetchrow: lectura de `existing` + el UPDATE ...RETURNING
    # de la ratificación -- NUNCA un INSERT INTO trd_series.
    assert mock_conn.fetchrow.call_count == 2
    assert not any(
        "INSERT INTO trd_series" in c.args[0] for c in mock_conn.fetchrow.call_args_list
    )

    clear_calls = [
        c.args
        for c in mock_conn.execute.call_args_list
        if "trd_revision_requerida = false" in c.args[0]
    ]
    assert len(clear_calls) == 1
    assert clear_calls[0][1] == serie_id
    assert clear_calls[0][2] == "cerrado_bajo_serie_migrada"

    actions = _audit_actions(mock_conn)
    assert len(actions) == 1
    assert actions[0][5] == "archive.trd_version_convalidada"
    import json as _json

    payload = _json.loads(actions[0][8])
    assert payload["ratificacion"] is True
    assert payload["expedientes_ratificados"] == 3


@pytest.mark.asyncio
async def test_convalidar_desde_migrada_solo_limpia_motivo_migrada(client_with_conn) -> None:
    """La ratificación SOLO debe limpiar expedientes con motivo
    `cerrado_bajo_serie_migrada` -- nunca `cierre_sin_snapshot_de_retencion`
    ni `serie_no_resoluble`. El SQL emitido lo garantiza filtrando por
    `trd_revision_motivo = $2`; este test fija ese contrato."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.execute.return_value = "UPDATE 0"
    code = "MIGR2"
    serie_id = uuid.uuid4()
    existing = make_trd_serie_record(serie_id=serie_id, code=code, version=1, estado="migrada")
    updated = make_trd_serie_record(
        serie_id=serie_id,
        code=code,
        version=1,
        estado="convalidada",
        acto_administrativo="Resolución 1",
        fecha_convalidacion=date(2026, 1, 1),
        instancia_convalidante="Consejo Departamental",
    )
    mock_conn.fetchrow.side_effect = [existing, updated]

    resp = await client.post(
        f"/api/v1/trd/{code}/versiones/1/convalidar",
        json={
            "acto_administrativo": "Resolución 1",
            "fecha_convalidacion": "2026-01-01",
            "instancia_convalidante": "Consejo Departamental",
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200, resp.text

    clear_call = next(
        c
        for c in mock_conn.execute.call_args_list
        if "trd_revision_requerida = false" in c.args[0]
    )
    sql = clear_call.args[0]
    assert "status IN ('closed', 'transferred')" in sql
    assert "trd_revision_motivo = $2" in sql
    assert clear_call.args[2] == "cerrado_bajo_serie_migrada"


# ---------------------------------------------------------------------------
# convalidar: transiciones ilegales
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("estado", ["borrador", "convalidada", "derogada"])
async def test_convalidar_illegal_transition_409(client_with_conn, estado: str) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    existing = make_trd_serie_record(code="CVILL", version=1, estado=estado)
    mock_conn.fetchrow.return_value = existing

    resp = await client.post(
        "/api/v1/trd/CVILL/versiones/1/convalidar",
        json={
            "acto_administrativo": "X",
            "fecha_convalidacion": "2026-01-01",
            "instancia_convalidante": "Y",
        },
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == "trd_transicion_invalida"
    assert mock_conn.fetchrow.call_count == 1


@pytest.mark.asyncio
async def test_convalidar_requires_acto_fields_422(client_with_conn) -> None:
    """El CHECK de BD (`trd_series_convalidada_acto_check`) ya lo exige;
    Pydantic debe dar un 422 legible SIN llegar a tocar la BD."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True

    resp = await client.post(
        "/api/v1/trd/CVREQ/versiones/1/convalidar",
        json={"acto_administrativo": "Resolución 1"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 422
    mock_conn.fetchrow.assert_not_called()


# ---------------------------------------------------------------------------
# derogar: convalidada/migrada -> derogada
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("estado", ["convalidada", "migrada"])
async def test_derogar_legal_transitions(client_with_conn, estado: str) -> None:
    client, mock_conn = client_with_conn
    # [0]=gate D-08/USUA_PERM_TRD, [1]=count_expedientes_open_por_trd_serie_code,
    # [2]=hash previo de la cadena de auditoria (orpycamcp_common.audit.append).
    mock_conn.fetchval.side_effect = [True, 0, None]
    code = "DEROK"
    existing = make_trd_serie_record(code=code, version=1, estado=estado)
    updated = make_trd_serie_record(
        code=code, version=1, estado="derogada", valid_to=date(2026, 7, 31)
    )
    mock_conn.fetchrow.side_effect = [existing, updated]

    resp = await client.post(
        f"/api/v1/trd/{code}/versiones/1/derogar", headers=TENANT_HEADERS
    )

    assert resp.status_code == 200, resp.text
    assert resp.json()["estado"] == "derogada"
    actions = _audit_actions(mock_conn)
    assert len(actions) == 1
    assert actions[0][5] == "archive.trd_version_derogada"
    import json as _json

    assert _json.loads(actions[0][8])["estado_anterior"] == estado


@pytest.mark.asyncio
@pytest.mark.parametrize("estado", ["borrador", "aprobada", "derogada"])
async def test_derogar_illegal_transition_409(client_with_conn, estado: str) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    existing = make_trd_serie_record(code="DERILL", version=1, estado=estado)
    mock_conn.fetchrow.return_value = existing

    resp = await client.post(
        "/api/v1/trd/DERILL/versiones/1/derogar", headers=TENANT_HEADERS
    )

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == "trd_transicion_invalida"
    # El gate de estado corta ANTES de consultar expedientes abiertos.
    assert mock_conn.fetchrow.call_count == 1


@pytest.mark.asyncio
async def test_derogar_bloqueada_por_expedientes_open(client_with_conn) -> None:
    """Migración 038, hallazgo #3 (decisión elegida: bloquear por
    expedientes `open`, no exigir sucesora ya convalidada -- ver docstring
    de `derogar_trd_serie_version`)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.side_effect = [True, 3]  # gate, luego 3 expedientes open
    code = "DEROBLK"
    existing = make_trd_serie_record(code=code, version=1, estado="convalidada")
    mock_conn.fetchrow.return_value = existing

    resp = await client.post(
        f"/api/v1/trd/{code}/versiones/1/derogar", headers=TENANT_HEADERS
    )

    assert resp.status_code == 409, resp.text
    assert (
        resp.json()["detail"]["error"] == "trd_derogacion_bloqueada_expedientes_abiertos"
    )
    # Nunca llegó a ejecutar el UPDATE de derogación.
    assert mock_conn.fetchrow.call_count == 1
    assert _audit_actions(mock_conn) == []


# ---------------------------------------------------------------------------
# registrar-rusd (migración 038, hallazgo #4)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_registrar_rusd_sobre_convalidada(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    code = "RUSD1"
    existing = make_trd_serie_record(code=code, version=1, estado="convalidada")
    updated = make_trd_serie_record(
        code=code,
        version=1,
        estado="convalidada",
        rusd_radicado="RUSD-2026-00987",
        fecha_publicacion=date(2026, 4, 1),
    )
    mock_conn.fetchrow.side_effect = [existing, updated]

    resp = await client.post(
        f"/api/v1/trd/{code}/versiones/1/registrar-rusd",
        json={"rusd_radicado": "RUSD-2026-00987", "fecha_publicacion": "2026-04-01"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["rusd_radicado"] == "RUSD-2026-00987"
    assert body["fecha_publicacion"] == "2026-04-01"
    actions = _audit_actions(mock_conn)
    assert len(actions) == 1
    assert actions[0][5] == "archive.trd_rusd_registrado"


@pytest.mark.asyncio
async def test_registrar_rusd_requires_at_least_one_field_422(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True

    resp = await client.post(
        "/api/v1/trd/RUSD2/versiones/1/registrar-rusd", json={}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 422
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_registrar_rusd_solo_sobre_convalidada_409(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    existing = make_trd_serie_record(code="RUSD3", version=1, estado="migrada")
    mock_conn.fetchrow.return_value = existing

    resp = await client.post(
        "/api/v1/trd/RUSD3/versiones/1/registrar-rusd",
        json={"rusd_radicado": "RUSD-1"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == "trd_version_no_convalidada"


@pytest.mark.asyncio
async def test_registrar_rusd_ya_registrado_409(client_with_conn) -> None:
    """Inmutable una vez puesto (trigger 038 + chequeo en servicio)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    existing = make_trd_serie_record(
        code="RUSD4", version=1, estado="convalidada", rusd_radicado="RUSD-YA"
    )
    mock_conn.fetchrow.return_value = existing

    resp = await client.post(
        "/api/v1/trd/RUSD4/versiones/1/registrar-rusd",
        json={"rusd_radicado": "RUSD-NUEVO"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 409, resp.text
    assert resp.json()["detail"]["error"] == "trd_rusd_ya_registrado"


# ---------------------------------------------------------------------------
# Permisos (mismo gate que PATCH/versiones -- USUA_PERM_TRD min_crud=3)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_derogar_forbidden_without_permission(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False

    resp = await client.post("/api/v1/trd/X/versiones/1/derogar", headers=TENANT_HEADERS)

    assert resp.status_code == 403
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_aprobar_forbidden_without_permission(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False

    resp = await client.post(
        "/api/v1/trd/X/versiones/1/aprobar", json=_APROBAR_BODY, headers=TENANT_HEADERS
    )

    assert resp.status_code == 403
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_convalidar_forbidden_without_permission(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False

    resp = await client.post(
        "/api/v1/trd/X/versiones/1/convalidar",
        json={
            "acto_administrativo": "X",
            "fecha_convalidacion": "2026-01-01",
            "instancia_convalidante": "Y",
        },
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 403
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_devolver_forbidden_without_permission(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False

    resp = await client.post(
        "/api/v1/trd/X/versiones/1/devolver",
        json={"motivo": "x"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 403
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_registrar_rusd_forbidden_without_permission(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False

    resp = await client.post(
        "/api/v1/trd/X/versiones/1/registrar-rusd",
        json={"rusd_radicado": "RUSD-1"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 403
    mock_conn.fetchrow.assert_not_called()


# ---------------------------------------------------------------------------
# GET /trd/revision-pendiente
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_revision_pendiente(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.side_effect = [True, 1]  # permiso, luego COUNT(*)
    # `caller_clearance` (resolve_caller_clearance) lee vía fetchrow: sin
    # membresías/is_root -> PUBLICA (1), determinista (mismo patrón que
    # `test_list_expedientes` en test_archive.py).
    mock_conn.fetchrow.return_value = None
    exp_id = uuid.uuid4()
    serie_id = uuid.uuid4()
    mock_conn.fetch.return_value = [
        {
            "id": exp_id,
            "code": "EXP-2026-0001",
            "status": "closed",
            "trd_serie_id": serie_id,
            "trd_serie_code": "100",
            "trd_revision_motivo": "cerrado_bajo_serie_migrada",
            "closed_at": None,
        }
    ]

    resp = await client.get("/api/v1/trd/revision-pendiente", headers=TENANT_HEADERS)

    assert resp.status_code == 200, resp.text
    assert resp.headers["X-Total-Count"] == "1"
    body = resp.json()
    assert len(body) == 1
    assert body[0]["id"] == str(exp_id)
    assert body[0]["trd_revision_motivo"] == "cerrado_bajo_serie_migrada"


@pytest.mark.asyncio
async def test_list_revision_pendiente_filters_by_clearance(client_with_conn) -> None:
    """Hallazgo de seguridad [Alta, BLOQUEANTE], migración 038: `USUA_PERM_TRD`
    es ORTOGONAL al clearance -- el COUNT y la página deben compartir el
    MISMO predicado `nivel_seguridad <= $1`, mismo criterio que
    `list_expedientes`."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.side_effect = [True, 0]
    mock_conn.fetchrow.return_value = None  # sin membresías -> PUBLICA (1)
    mock_conn.fetch.return_value = []

    resp = await client.get("/api/v1/trd/revision-pendiente", headers=TENANT_HEADERS)

    assert resp.status_code == 200, resp.text
    count_sql, count_clearance = mock_conn.fetchval.call_args_list[1][0]
    assert "nivel_seguridad <= $1" in count_sql
    assert count_clearance == 1

    page_args = mock_conn.fetch.call_args[0]
    assert "nivel_seguridad <= $1" in page_args[0]
    assert page_args[1] == 1


@pytest.mark.asyncio
async def test_list_revision_pendiente_audita_consulta(client_with_conn) -> None:
    """RF-BUS-10: la traza AGREGADA se emite con `X-User-Id`/`X-Canal`
    presentes -- antes este listado de expedientes clasificados no dejaba
    ningún rastro de quién lo consultó."""
    client, mock_conn = client_with_conn
    # [0]=gate, [1]=COUNT(*), [2]=hash previo de audit.append.
    mock_conn.fetchval.side_effect = [True, 0, None]
    mock_conn.fetchrow.return_value = None
    mock_conn.fetch.return_value = []
    user_id = uuid.uuid4()

    resp = await client.get(
        "/api/v1/trd/revision-pendiente",
        headers={**TENANT_HEADERS, "X-User-Id": str(user_id), "X-Canal": "ui"},
    )

    assert resp.status_code == 200, resp.text
    actions = _audit_actions(mock_conn)
    assert len(actions) == 1
    assert actions[0][5] == "archive.expediente_revision_pendiente_listado"


@pytest.mark.asyncio
async def test_list_revision_pendiente_forbidden_without_permission(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False

    resp = await client.get("/api/v1/trd/revision-pendiente", headers=TENANT_HEADERS)

    assert resp.status_code == 403
    mock_conn.fetch.assert_not_called()


@pytest.mark.asyncio
async def test_list_revision_pendiente_route_not_captured_by_serie_id(client_with_conn) -> None:
    """Prueba de regresión de ordering de rutas: `/revision-pendiente` NO debe
    caer en `GET /{serie_id}` (que reventaría con un 422 al no parsear como
    UUID) -- confirma que quedó declarada ANTES en `trd.py`."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.side_effect = [True, 0]
    mock_conn.fetchrow.return_value = None
    mock_conn.fetch.return_value = []

    resp = await client.get("/api/v1/trd/revision-pendiente", headers=TENANT_HEADERS)

    assert resp.status_code == 200
