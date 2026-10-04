# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Transferencias: gate del índice firmado al PREPARAR (AREA-07-03), el acta ata la firma
del índice (AREA-07-04), tenencia leída en la transacción y par sin tenencia (AREA-07-06),
FK carrera -> 422 con asiento (AREA-07-08) y `cabecera_completa` (AREA-07-11).

Carril mockeado: el orden de `fetchrow` de `create()` es expediente, índice vigente,
[ubicaciones], dependencia, INSERT; el de `recibir()` el de `test_transferencias.py`.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import asyncpg
import pytest

from app.services.transferencia_service import TransferenciaService, _cabecera_completa
from tests.conftest import (
    TENANT_HEADERS,
    USER_ID,
    indice_firmado_row,
    make_expediente_record,
)

NOW = datetime.now(tz=UTC)
_OBJETO = "Transferencia de la serie de contratos de la vigencia"


def _audit(mock_conn) -> list[tuple]:
    return [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]


async def _post(client, exp_id, origen=None, destino=None):
    body = {"tipo": "primaria", "expediente_id": str(exp_id), "objeto": _OBJETO}
    if origen:
        body["ubicacion_origen_id"] = str(origen)
    if destino:
        body["ubicacion_destino_id"] = str(destino)
    return await client.post("/api/v1/transferencias", json=body, headers=TENANT_HEADERS)


def _ubicacion(uid, tipo_archivo):
    return {
        "id": uid, "parent_id": None, "nivel": "deposito", "codigo": "UB", "nombre": "x",
        "tipo_archivo": tipo_archivo, "capacidad_ml": None, "activo": True, "created_at": NOW,
    }


def _unidad(ubicacion_id):
    return {
        "unidad_id": uuid.uuid4(), "tipo": "caja", "codigo": "C", "signatura": "S",
        "ubicacion_id": ubicacion_id, "folio_inicio": 1, "folio_fin": 2,
    }


# --- AREA-07-03: gate del índice al preparar --------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("indice", [{"id": uuid.uuid4(), "estado": "pendiente_firma"}, None])
async def test_create_con_indice_sin_firmar_409_y_asiento(client_with_conn, indice) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    exp_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        indice,
    ]
    mock_conn.fetch.return_value = []

    resp = await _post(client, exp_id)

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "indice_no_firmado"
    denegacion = next(a for a in _audit(mock_conn) if a[5] == "archive.transferencia_preparada_failed")
    assert json.loads(denegacion[8])["motivo"] == "indice_no_firmado"
    # nada se insertó
    assert not any("INSERT INTO transferencias" in str(c.args[0]) for c in mock_conn.fetchrow.call_args_list)


@pytest.mark.asyncio
async def test_create_con_indice_firmado_si_prepara(client_with_conn) -> None:
    """Control del control: el mismo flujo con el índice firmado llega al INSERT."""
    from tests.test_transferencias import _depe_row, _transfer_row

    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    exp_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        indice_firmado_row(),
        _depe_row(),
        _transfer_row(expediente_id=exp_id),
    ]
    mock_conn.fetch.return_value = []

    resp = await _post(client, exp_id)

    assert resp.status_code == 201, resp.text


# --- AREA-07-06: tenencia en la tx y par sin tenencia -------------------------


@pytest.mark.asyncio
async def test_par_sin_tenencia_se_rechaza_422(client_with_conn) -> None:
    """Un expediente sin unidades y el par aportado: 422 `ubicacion_sin_tenencia` (antes se
    aceptaba sin cotejo)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    exp_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        indice_firmado_row(),
    ]
    mock_conn.fetch.side_effect = [[], []]  # radicados, unidades (ninguna)

    resp = await _post(client, exp_id, uuid.uuid4(), uuid.uuid4())

    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "ubicacion_sin_tenencia"
    denegacion = next(a for a in _audit(mock_conn) if a[5] == "archive.transferencia_preparada_failed")
    assert json.loads(denegacion[8])["motivo"] == "ubicacion_sin_tenencia"


@pytest.mark.asyncio
async def test_la_tenencia_se_lee_con_for_share_dentro_de_la_transaccion(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    exp_id = uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        indice_firmado_row(),
    ]
    mock_conn.fetch.side_effect = [[], []]
    eventos: list[str] = []
    real_tx = mock_conn.transaction

    def _tx(*a, **k):
        eventos.append("tx")
        return real_tx(*a, **k)

    mock_conn.transaction = _tx
    original_fetch = mock_conn.fetch

    async def _fetch(sql, *a, **k):
        if "FROM expediente_unidad" in sql:
            eventos.append("FOR SHARE" if "FOR SHARE" in sql else "unidades_sin_lock")
        return await original_fetch(sql, *a, **k)

    mock_conn.fetch = _fetch

    await _post(client, exp_id, uuid.uuid4(), uuid.uuid4())

    assert eventos.index("tx") < eventos.index("FOR SHARE"), eventos


# --- AREA-07-08: FK en la carrera -> 422 con asiento --------------------------


@pytest.mark.asyncio
async def test_fk_de_ubicacion_en_la_carrera_es_422_y_deja_asiento(client_with_conn) -> None:
    from tests.test_transferencias import _depe_row

    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    exp_id, origen, destino = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        indice_firmado_row(),
        _ubicacion(origen, "gestion"),
        _ubicacion(destino, "central"),
        _depe_row(),
        asyncpg.ForeignKeyViolationError("violates foreign key constraint"),
    ]
    mock_conn.fetch.side_effect = [[], [_unidad(origen)]]

    resp = await _post(client, exp_id, origen, destino)

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"]["error"] == "ubicacion_not_found"
    denegacion = next(a for a in _audit(mock_conn) if a[5] == "archive.transferencia_preparada_failed")
    assert json.loads(denegacion[8])["motivo"] == "ubicacion_not_found"


# --- AREA-07-04: el acta ata la firma del índice ------------------------------


def _mock_sellar():
    return patch.object(
        TransferenciaService, "_sellar_acta_post_commit", new=AsyncMock(return_value=None)
    )


def _flujo_recibir(mock_conn, tid, exp_id, *, indice_gate, indice_fixity, version_leida=None):
    from tests.test_transferencias import _transfer_row

    secuencia = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="enviada"),
        indice_gate,
        make_expediente_record(exp_id=exp_id, exp_status="closed"),
        indice_fixity,
    ]
    if version_leida is not None:
        secuencia.append(version_leida)
    secuencia += [
        _transfer_row(
            id=tid, expediente_id=exp_id, estado="recibida", recibida_at=NOW,
            decidida_por=str(USER_ID),
        ),
        make_expediente_record(exp_id=exp_id, exp_status="transferred"),
    ]
    mock_conn.fetchrow.side_effect = secuencia
    mock_conn.fetch.return_value = []


def _datos_acta(mock_conn) -> dict:
    llamada = next(
        c for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and c.args[0].startswith("INSERT INTO transferencia_acta (")
    )
    cols = llamada.args[0].split("(", 2)[1].split(")", 1)[0].split(", ")
    return dict(zip(cols, llamada.args[1:], strict=True))


@pytest.mark.asyncio
async def test_acta_ata_la_firma_del_indice(client_with_conn) -> None:
    from tests.test_transferencias import _indice_row

    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    tid, exp_id, firma = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    fila = _indice_row(firma_id=firma)
    _flujo_recibir(mock_conn, tid, exp_id, indice_gate=fila, indice_fixity=fila)

    with _mock_sellar():
        resp = await client.post(f"/api/v1/transferencias/{tid}/recibir", headers=TENANT_HEADERS)

    assert resp.status_code == 200, resp.text
    datos = _datos_acta(mock_conn)
    assert datos["indice_firma_id"] == firma
    assert datos["indice_sha256"] == fila["xml_sha256"]
    # y DENTRO del XML que se hashea y se sella, no solo en una columna
    assert str(firma) in datos["acta_fuid_xml"]
    assert fila["xml_sha256"] in datos["acta_fuid_xml"]


@pytest.mark.asyncio
async def test_si_el_indice_cambio_entre_el_preflight_y_la_fixity_se_ata_la_que_se_verifico(
    client_with_conn,
) -> None:
    """El pre-flight vio la versión 1 (firma A); la fixity se calculó sobre la 0 (firma B):
    el acta señala el sello de LO QUE SE VERIFICÓ."""
    from tests.test_transferencias import _indice_row

    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    firma_a, firma_b = uuid.uuid4(), uuid.uuid4()
    _flujo_recibir(
        mock_conn, tid, exp_id,
        indice_gate=_indice_row(version=1, firma_id=firma_a),
        indice_fixity=_indice_row(version=0, firma_id=firma_b),
        version_leida=_indice_row(version=0, firma_id=firma_b),
    )

    with _mock_sellar():
        resp = await client.post(f"/api/v1/transferencias/{tid}/recibir", headers=TENANT_HEADERS)

    assert resp.status_code == 200, resp.text
    datos = _datos_acta(mock_conn)
    assert datos["indice_firma_id"] == firma_b
    assert datos["indice_version"] == 0


@pytest.mark.asyncio
async def test_si_la_version_verificada_no_esta_firmada_no_se_recibe(client_with_conn) -> None:
    from tests.test_transferencias import _indice_row

    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    _flujo_recibir(
        mock_conn, tid, exp_id,
        indice_gate=_indice_row(version=1),
        indice_fixity=_indice_row(version=0),
        version_leida=_indice_row(version=0, estado="pendiente_firma"),
    )

    with _mock_sellar():
        resp = await client.post(f"/api/v1/transferencias/{tid}/recibir", headers=TENANT_HEADERS)

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "indice_no_firmado"


# --- AREA-07-11: cabecera_completa derivado -----------------------------------

_NS = "urn:orpycamcp:fuid:v1"


def _acta_xml(unidad="Secretaria", oficina="Oficina", objeto="Objeto de la transferencia"):
    return (
        f'<fuid xmlns="{_NS}"><cabecera><entidadProductora>E</entidadProductora>'
        f"<unidadAdministrativa>{unidad}</unidadAdministrativa>"
        f"<oficinaProductora>{oficina}</oficinaProductora><objeto>{objeto}</objeto>"
        "</cabecera></fuid>"
    )


def test_cabecera_completa_con_las_tres_entradas() -> None:
    assert _cabecera_completa(_acta_xml()) is True


@pytest.mark.parametrize(
    "kw", [{"oficina": ""}, {"unidad": ""}, {"objeto": "   "}], ids=["oficina", "unidad", "objeto"]
)
def test_cabecera_incompleta_se_declara(kw) -> None:
    assert _cabecera_completa(_acta_xml(**kw)) is False


def test_xml_ilegible_no_cuenta_como_completo() -> None:
    assert _cabecera_completa("<fuid") is False
    assert _cabecera_completa("<fuid/>") is False


@pytest.mark.asyncio
async def test_get_acta_expone_cabecera_completa_false_para_un_acta_anterior(client_with_conn) -> None:
    from tests.test_transferencias import _acta_firma_row, _acta_row, _transfer_row

    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    tid, exp_id = uuid.uuid4(), uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        _transfer_row(id=tid, expediente_id=exp_id, estado="recibida"),
        None,  # caller_clearance: usuario no resoluble -> PUBLICA(1)
        make_expediente_record(exp_id=exp_id, nivel_seguridad=1),
        _acta_row(transferencia_id=tid, acta_fuid_xml=_acta_xml(oficina="")),
        _acta_firma_row(transferencia_id=tid),
    ]
    mock_conn.fetch.return_value = []

    resp = await client.get(f"/api/v1/transferencias/{tid}/acta", headers=TENANT_HEADERS)

    assert resp.status_code == 200, resp.text
    assert resp.json()["cabecera_completa"] is False
    assert resp.json()["indice_firma_id"] is None  # actas anteriores a la 053: sin inventar
