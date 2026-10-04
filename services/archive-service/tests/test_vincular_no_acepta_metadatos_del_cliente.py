# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Vincular un radicado NO sella lo que escriba quien vincula (AREA-07-02 y
AREA-06-08).

El índice electrónico se FIRMA con XAdES al cerrar el expediente. Hasta aquí el
número de radicado, la tipología, la dependencia productora, el formato, el
tamaño y la huella que traía el cuerpo se aceptaban tal cual («lo explícito
manda»), y ni siquiera se comprobaba que el radicado existiera: un archivista
nivel 2 podía meter en el índice un número oficial ajeno o inventado. El allowlist
del BFF no protege el backend —el cliente Keycloak del frontend es público
(PKCE) y el MCP expone la tool con campos libres—, así que la garantía vive aquí:
*un dato probatorio lo resuelve el servidor*.

Lo que se prueba, cada cosa con el INSERT como testigo (si el vínculo se
escribió, el test lo ve):

* un valor declarado que no coincide con lo que guarda document-service → 422
  `metadato_no_coincide`, **sin INSERT** y con asiento de denegación;
* un radicado que document-service no conoce → 404, sin INSERT;
* document-service caído → 503 (fail-closed para existencia y número);
* lo que se persiste es lo RESUELTO, aunque el cuerpo no declare nada.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import (
    EXPEDIENTE_ID,
    RADICADO_ID,
    TENANT_HEADERS,
    TRACKING_NUMBER,
    make_expediente_record,
    make_radicado_link_record,
    registrar_radicado,
)

_RUTA = f"/api/v1/expedientes/{EXPEDIENTE_ID}/radicados"
_INSERT = "INSERT INTO expediente_radicados"


def _armar(mock_conn) -> None:
    exp = make_expediente_record(exp_id=EXPEDIENTE_ID)
    link = make_radicado_link_record(expediente_id=EXPEDIENTE_ID, radicado_id=RADICADO_ID)
    mock_conn.fetchrow.side_effect = [exp, None, link, exp, None, exp]
    mock_conn.fetch.return_value = []
    mock_conn.fetchval.side_effect = [True, 0, 1, None, None]


def _insert(mock_conn):
    """Los argumentos del INSERT del vínculo, o None si no se ejecutó."""
    for llamada in mock_conn.fetchrow.await_args_list:
        if llamada.args and isinstance(llamada.args[0], str) and _INSERT in llamada.args[0]:
            return llamada.args
    return None


def _asientos(mock_conn, accion: str) -> list:
    return [
        c.args
        for c in mock_conn.execute.call_args_list
        if c.args
        and isinstance(c.args[0], str)
        and "INSERT INTO public.audit_log" in c.args[0]
        and accion in c.args
    ]


def _huellas_vacias():
    return patch(
        "app.services.archive_service.ArchiveService._huellas_por_radicado",
        AsyncMock(return_value={}),
    )


@pytest.fixture
def radicado_real():
    """Lo que document-service guarda del radicado de los fixtures."""
    return registrar_radicado(
        TRACKING_NUMBER,
        radicado_id=RADICADO_ID,
        doc_class="COR-PET",
        origin_dept="Juridica",
        origin_dept_code=120,
        formato="application/pdf",
        tamano_bytes=2048,
    )


async def _vincular(client, **cuerpo):
    return await client.post(
        _RUTA,
        json={"radicado_id": str(RADICADO_ID), "tracking_number": TRACKING_NUMBER, **cuerpo},
        headers=TENANT_HEADERS,
    )


@pytest.mark.asyncio
async def test_un_tracking_number_distinto_al_del_radicado_se_rechaza(
    client_with_conn, radicado_real
) -> None:
    """(a) El número oficial sellado en el índice es el del radicado, no el que
    escriba quien vincula."""
    client, mock_conn = client_with_conn
    _armar(mock_conn)

    with _huellas_vacias():
        resp = await _vincular(client, tracking_number="2026-AJENO-E-999999")

    assert resp.status_code == 422
    detalle = resp.json()["detail"]
    assert detalle["error"] == "metadato_no_coincide"
    assert detalle["desacuerdos"] == {str(RADICADO_ID): ["tracking_number"]}
    # El error nombra el CAMPO y nunca el valor resuelto: no es un oráculo de
    # números de radicado.
    assert TRACKING_NUMBER not in resp.text
    assert _insert(mock_conn) is None
    # Y la denegación deja constancia.
    assert _asientos(mock_conn, "archive.radicado_vinculo_denegado")


@pytest.mark.asyncio
async def test_una_dependencia_productora_ajena_se_rechaza(
    client_with_conn, radicado_real
) -> None:
    """(b) Quien vincula suele ser un archivista central, no la oficina que
    produjo el documento: el dato que aportaría es el equivocado por
    construcción."""
    client, mock_conn = client_with_conn
    _armar(mock_conn)

    with _huellas_vacias():
        resp = await _vincular(
            client,
            dependencia_productora_codigo=120,
            dependencia_productora_nombre="Otra",
        )

    assert resp.status_code == 422
    assert resp.json()["detail"]["desacuerdos"] == {
        str(RADICADO_ID): ["dependencia_productora"]
    }
    assert _insert(mock_conn) is None


@pytest.mark.asyncio
async def test_un_radicado_que_no_existe_en_document_service_es_404(
    client_with_conn,
) -> None:
    """(c) Antes ni se verificaba que existiera: se podía vincular un UUID
    cualquiera con un número inventado."""
    client, mock_conn = client_with_conn
    _armar(mock_conn)
    fantasma = uuid.uuid4()

    with _huellas_vacias():
        resp = await client.post(
            _RUTA,
            json={"radicado_id": str(fantasma), "tracking_number": "2026-X-E-000777"},
            headers=TENANT_HEADERS,
        )

    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "radicado_no_encontrado"
    assert _insert(mock_conn) is None


@pytest.mark.asyncio
async def test_una_huella_distinta_a_la_del_almacen_se_rechaza(
    client_with_conn, radicado_real
) -> None:
    """(d) La huella que firma el índice es la que storage guarda."""
    client, mock_conn = client_with_conn
    _armar(mock_conn)

    with patch(
        "app.services.archive_service.ArchiveService._huellas_por_radicado",
        AsyncMock(return_value={str(RADICADO_ID): "a" * 64}),
    ):
        resp = await _vincular(client, content_hash="b" * 64)

    assert resp.status_code == 422
    assert resp.json()["detail"]["desacuerdos"] == {str(RADICADO_ID): ["content_hash"]}
    assert _insert(mock_conn) is None


@pytest.mark.asyncio
async def test_se_persiste_lo_RESUELTO_aunque_el_cuerpo_no_declare_nada(
    client_with_conn, radicado_real
) -> None:
    """El control positivo de los anteriores: sin él, un resolutor que
    rechazara todo pasaba los cuatro. Tipología, productora, formato, tamaño y
    huella llegan al INSERT desde document-service y storage."""
    client, mock_conn = client_with_conn
    _armar(mock_conn)

    with patch(
        "app.services.archive_service.ArchiveService._huellas_por_radicado",
        AsyncMock(return_value={str(RADICADO_ID): "a" * 64}),
    ):
        resp = await _vincular(client)

    assert resp.status_code == 201
    args = _insert(mock_conn)
    assert args is not None
    # (sql, expediente, radicado, tracking, hash, formato, tamano, folios,
    #  orden, dep_codigo, dep_nombre, tipologia)
    assert args[3] == TRACKING_NUMBER
    assert args[4] == "a" * 64
    assert args[5] == "application/pdf"
    assert args[6] == 2048
    assert (args[9], args[10]) == (120, "Juridica")
    assert args[11] == "COR-PET"


@pytest.mark.asyncio
async def test_una_tipologia_falsa_no_llega_al_indice(
    client_with_conn, radicado_real
) -> None:
    """AREA-06-08: `payload.tipologia` se escribía tal cual en un índice que se
    firma. Con 'FALSA' frente a 'COR-PET' no se vincula; coincidiendo, sí, y
    queda la resuelta."""
    client, mock_conn = client_with_conn
    _armar(mock_conn)
    with _huellas_vacias():
        falsa = await _vincular(client, tipologia="FALSA")
    assert falsa.status_code == 422
    assert falsa.json()["detail"]["desacuerdos"] == {str(RADICADO_ID): ["tipologia"]}
    assert _insert(mock_conn) is None

    _armar(mock_conn)
    with _huellas_vacias():
        buena = await _vincular(client, tipologia="COR-PET")
    assert buena.status_code == 201
    assert _insert(mock_conn)[11] == "COR-PET"


@pytest.mark.asyncio
async def test_si_document_service_no_responde_NO_se_vincula(client_with_conn) -> None:
    """Fail-closed para existencia y número: un 503 explícito, no un vínculo
    con datos sin contrastar."""
    from app.clients.document_client import DocumentClientError

    client, mock_conn = client_with_conn
    _armar(mock_conn)

    with patch(
        "app.clients.document_client.fetch_radicado_metadata_internal",
        AsyncMock(side_effect=DocumentClientError("caído")),
    ):
        resp = await _vincular(client)

    assert resp.status_code == 503
    assert resp.json()["detail"]["error"] == "document_service_no_disponible"
    assert _insert(mock_conn) is None


@pytest.mark.asyncio
async def test_el_lote_aborta_entero_si_un_item_no_coincide(client_with_conn) -> None:
    """Todo-o-nada también en el cotejo: ningún vínculo parcial."""
    client, mock_conn = client_with_conn
    r1 = registrar_radicado("2026-X-E-000001")
    r2 = registrar_radicado("2026-X-E-000002")
    exp = make_expediente_record(exp_id=EXPEDIENTE_ID, exp_status="open")
    mock_conn.fetchrow.side_effect = [exp, None]
    mock_conn.fetchval.side_effect = [True, None, None]

    with _huellas_vacias():
        resp = await client.post(
            f"{_RUTA}/batch",
            json={
                "radicados": [
                    {"radicado_id": str(r1), "tracking_number": "2026-X-E-000001"},
                    {"radicado_id": str(r2), "tracking_number": "2026-X-E-999999"},
                ]
            },
            headers=TENANT_HEADERS,
        )

    assert resp.status_code == 422
    assert resp.json()["detail"]["desacuerdos"] == {str(r2): ["tracking_number"]}
    assert _insert(mock_conn) is None


def _document_service_con_nivel(nivel: int, **meta):
    """Doble que HONRA `max_clearance`, como el real: un radicado por encima
    del clearance pedido no aparece."""

    async def _fake(*, tenant_slug, radicado_ids, max_clearance):
        if nivel > max_clearance:
            return {}
        return {
            str(RADICADO_ID): {
                "subject": "s",
                "doc_type": "E",
                "tracking_number": TRACKING_NUMBER,
                "doc_class": "COR-PET",
                "origin_dept": "Juridica",
                "origin_dept_code": 120,
                "formato": "application/pdf",
                "tamano_bytes": 2048,
                **meta,
            }
        }

    return patch(
        "app.clients.document_client.fetch_radicado_metadata_internal",
        AsyncMock(side_effect=_fake),
    )


@pytest.mark.asyncio
async def test_un_radicado_reservado_no_es_un_oraculo_de_metadatos(client_with_conn) -> None:
    """Hallazgo medio de T6: con el UUID de un radicado nivel 3, un llamante de
    clearance 1 recibia `desacuerdos` con los NOMBRES de los campos erroneos
    (tipologia, formato, productora, huella): un oraculo de lectura. Ahora el
    cuerpo es el mismo con la tipologia correcta y con la falsa, y el mismo que
    un radicado inexistente. Mutacion: pedir `CLEARANCE_SIN_RESTRICCION` en
    `_resolver_metadatos_vinculo` tumba este test."""
    client, mock_conn = client_with_conn
    cuerpos = []
    with _document_service_con_nivel(3), _huellas_vacias():
        for extra in ({"tipologia": "FALSA"}, {"tipologia": "COR-PET"}, {}):
            _armar(mock_conn)
            resp = await _vincular(client, **extra)
            assert resp.status_code == 404
            cuerpos.append(resp.json())
    assert cuerpos[0] == cuerpos[1] == cuerpos[2]
    assert "desacuerdos" not in cuerpos[0]["detail"]
    assert _insert(mock_conn) is None
    assert _asientos(mock_conn, "archive.radicado_vinculo_denegado") == []

    # Control positivo: el mismo radicado a nivel 1 SI se coteja y vincula.
    _armar(mock_conn)
    with _document_service_con_nivel(1), _huellas_vacias():
        assert (await _vincular(client)).status_code == 201


@pytest.mark.asyncio
async def test_un_radicado_sin_tipificar_no_se_vincula(client_with_conn) -> None:
    """Sin `doc_class` el indice se firmaria con `<tipologia>` vacia (obligatoria
    en el XSD) y sin forma de corregirla. 422 `radicado_sin_tipificar`, haya o
    no tipologia declarada; no se escribe nada. Mutacion: quitar el `raise` en
    el resolutor lo tumba (se persistiria NULL)."""
    client, mock_conn = client_with_conn
    with _document_service_con_nivel(1, doc_class=None), _huellas_vacias():
        for extra in ({}, {"tipologia": "COR-PET"}):
            _armar(mock_conn)
            resp = await _vincular(client, **extra)
            assert resp.status_code == 422
            assert resp.json()["detail"]["error"] == "radicado_sin_tipificar"
    assert _insert(mock_conn) is None
