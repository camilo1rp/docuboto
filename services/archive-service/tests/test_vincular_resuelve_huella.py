# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La huella del documento se resuelve EN EL SERVIDOR al vincular (E15 T-08).

El defecto: el índice electrónico debe llevar la huella de cada documento
(RT-15 art. 4.3.2.3) y el sistema SIEMPRE la ha tenido —storage-service la
calcula al subir el anexo—; lo que no había era quien la trajera. La pantalla de
vincular manda `radicado_id` y `tracking_number`, así que `content_hash` quedaba
nulo para **todo lo vinculado desde la interfaz** y el índice se autodeclaraba
sin huellas teniendo los ficheros con su SHA-256 al lado.

No se le pide al cliente por el mismo motivo por el que el departamento se
deriva del municipio: una huella aportada por quien vincula no prueba nada
sobre lo que el archivo custodia.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.services.archive_service import ArchiveService

_RAD = uuid.uuid4()
_FILE = uuid.uuid4()
_HASH = "a" * 64


def _servicio() -> ArchiveService:
    return ArchiveService(AsyncMock(), "demo")


def _anexos(**kwargs):
    return patch(
        "app.clients.document_client.fetch_anexos_internal",
        AsyncMock(**kwargs),
    )


def _huellas(**kwargs):
    return patch(
        "app.clients.storage_client.fetch_file_hashes_internal",
        AsyncMock(**kwargs),
    )


@pytest.mark.asyncio
async def test_la_huella_sale_del_anexo_que_el_sistema_ya_tenia() -> None:
    with (
        _anexos(return_value={str(_RAD): [{"file_id": str(_FILE), "filename": "of.pdf"}]}),
        _huellas(return_value={str(_FILE): _HASH}),
    ):
        assert await _servicio()._huellas_por_radicado([_RAD]) == {str(_RAD): _HASH}


@pytest.mark.asyncio
async def test_el_inventario_se_pide_SIN_recorte_de_clearance() -> None:
    """Es un acto de sistema componiendo el índice, no una lectura por una
    persona. Recortarlo dejaría la completitud del índice dependiendo de quién
    vinculó: dos archivistas producirían índices distintos del mismo
    expediente, que es justo lo que un índice no puede permitirse."""
    anexos = AsyncMock(return_value={})
    with patch("app.clients.document_client.fetch_anexos_internal", anexos), _huellas(
        return_value={}
    ):
        await _servicio()._huellas_por_radicado([_RAD])
    assert anexos.await_args.kwargs["max_clearance"] is None


@pytest.mark.asyncio
async def test_un_radicado_SIN_anexos_no_recibe_huella_inventada() -> None:
    """Es lo normal en ventanilla y no es un fallo de integridad: el índice lo
    declara como «sin huella» y la fixity se predica de lo que sí la tiene."""
    with _anexos(return_value={str(_RAD): []}), _huellas(return_value={}) as h:
        assert await _servicio()._huellas_por_radicado([_RAD]) == {}
    h.assert_not_awaited()


@pytest.mark.asyncio
async def test_si_document_service_no_responde_el_VINCULO_no_se_pierde() -> None:
    """Best-effort a propósito: vincular un radicado a su expediente es un acto
    archivístico que no puede quedar preso de que otros dos servicios estén en
    pie. Sin huella se declara; abortar cambiaría un dato ausente por trabajo
    perdido."""
    from app.clients.document_client import DocumentClientError

    with _anexos(side_effect=DocumentClientError("caído")), _huellas(return_value={}):
        assert await _servicio()._huellas_por_radicado([_RAD]) == {}


@pytest.mark.asyncio
async def test_si_storage_no_responde_tampoco() -> None:
    with (
        _anexos(return_value={str(_RAD): [{"file_id": str(_FILE), "filename": "of.pdf"}]}),
        _huellas(return_value={}),
    ):
        assert await _servicio()._huellas_por_radicado([_RAD]) == {}


@pytest.mark.asyncio
async def test_un_anexo_borrado_no_aporta_huella() -> None:
    """storage omite del resultado los ficheros borrados. Un id sin huella no
    se completa con la de otro anexo del mismo radicado por descuido: el
    documento que queda es el de los ficheros vivos."""
    otro = uuid.uuid4()
    with (
        _anexos(
            return_value={
                str(_RAD): [
                    {"file_id": str(_FILE), "filename": "vivo.pdf"},
                    {"file_id": str(otro), "filename": "borrado.pdf"},
                ]
            }
        ),
        _huellas(return_value={str(_FILE): _HASH}),
    ):
        assert await _servicio()._huellas_por_radicado([_RAD]) == {str(_RAD): _HASH}


@pytest.mark.asyncio
async def test_el_lote_pregunta_UNA_vez_por_todos() -> None:
    """Un viaje por anexo convertiría el cierre de un expediente de cien
    documentos en cien viajes de red."""
    otro_rad, otro_file = uuid.uuid4(), uuid.uuid4()
    anexos = AsyncMock(
        return_value={
            str(_RAD): [{"file_id": str(_FILE), "filename": "a.pdf"}],
            str(otro_rad): [{"file_id": str(otro_file), "filename": "b.pdf"}],
        }
    )
    huellas = AsyncMock(return_value={str(_FILE): _HASH, str(otro_file): "b" * 64})
    with (
        patch("app.clients.document_client.fetch_anexos_internal", anexos),
        patch("app.clients.storage_client.fetch_file_hashes_internal", huellas),
    ):
        resultado = await _servicio()._huellas_por_radicado([_RAD, otro_rad])

    assert anexos.await_count == 1
    assert huellas.await_count == 1
    assert resultado == {str(_RAD): _HASH, str(otro_rad): "b" * 64}


# ---------------------------------------------------------------------------
# El cableado: que la huella resuelta LLEGUE al INSERT del vínculo.
#
# Sin esto, el resolutor de arriba podría funcionar perfectamente sin que nadie
# lo llamara — y el índice seguiría sin huellas con la suite en verde. Es el
# mismo hueco que ya apareció en este proyecto: probar el arreglo en vez del
# camino que lo usa.
# ---------------------------------------------------------------------------


def _content_hash_del_insert(mock_conn) -> str | None:
    """El 4.º parámetro del INSERT de `expediente_radicados` (ver
    `ArchiveRepository.link_radicado`: expediente, radicado, tracking, hash)."""
    for llamada in mock_conn.fetchrow.await_args_list:
        sql = llamada.args[0] if llamada.args else ""
        if isinstance(sql, str) and "INSERT INTO expediente_radicados" in sql:
            return llamada.args[4]
    raise AssertionError("no se ejecutó el INSERT del vínculo")


@pytest.mark.asyncio
async def test_al_vincular_desde_la_pantalla_el_indice_SI_recibe_la_huella(
    client_with_conn,
) -> None:
    """El defecto, en el camino real: la pantalla manda dos campos y el vínculo
    quedaba sin huella aunque el radicado tuviera un anexo con SHA-256."""
    from tests.conftest import make_expediente_record, make_radicado_link_record
    from tests.test_archive import EXPEDIENTE_ID, RADICADO_ID, TENANT_HEADERS, TRACKING_NUMBER

    client, mock_conn = client_with_conn
    exp = make_expediente_record(exp_id=EXPEDIENTE_ID)
    link = make_radicado_link_record(expediente_id=EXPEDIENTE_ID, radicado_id=RADICADO_ID)
    mock_conn.fetchrow.side_effect = [exp, None, link, exp, None, exp]
    mock_conn.fetch.return_value = []
    mock_conn.fetchval.side_effect = [True, 0, 1, None, None]

    with (
        _anexos(
            return_value={str(RADICADO_ID): [{"file_id": str(_FILE), "filename": "of.pdf"}]}
        ),
        _huellas(return_value={str(_FILE): _HASH}),
    ):
        resp = await client.post(
            f"/api/v1/expedientes/{EXPEDIENTE_ID}/radicados",
            json={"radicado_id": str(RADICADO_ID), "tracking_number": TRACKING_NUMBER},
            headers=TENANT_HEADERS,
        )

    assert resp.status_code == 201
    assert _content_hash_del_insert(mock_conn) == _HASH


@pytest.mark.asyncio
async def test_la_huella_que_el_llamante_declara_MANDA_sobre_la_resuelta(
    client_with_conn,
) -> None:
    """El campo del contrato sigue sirviendo a quien la conoce de primera mano
    (importación con fixity, E11 INT-05): ahí la huella viene del paquete que
    se está recibiendo y es la que hay que conservar."""
    from tests.conftest import make_expediente_record, make_radicado_link_record
    from tests.test_archive import EXPEDIENTE_ID, RADICADO_ID, TENANT_HEADERS, TRACKING_NUMBER

    client, mock_conn = client_with_conn
    exp = make_expediente_record(exp_id=EXPEDIENTE_ID)
    link = make_radicado_link_record(expediente_id=EXPEDIENTE_ID, radicado_id=RADICADO_ID)
    mock_conn.fetchrow.side_effect = [exp, None, link, exp, None, exp]
    mock_conn.fetch.return_value = []
    mock_conn.fetchval.side_effect = [True, 0, 1, None, None]

    declarada = "d" * 64
    anexos = AsyncMock(return_value={})
    with patch("app.clients.document_client.fetch_anexos_internal", anexos), _huellas(
        return_value={}
    ):
        resp = await client.post(
            f"/api/v1/expedientes/{EXPEDIENTE_ID}/radicados",
            json={
                "radicado_id": str(RADICADO_ID),
                "tracking_number": TRACKING_NUMBER,
                "content_hash": declarada.upper(),
            },
            headers=TENANT_HEADERS,
        )

    assert resp.status_code == 201
    assert _content_hash_del_insert(mock_conn) == declarada
    # Y no se molesta a document-service para algo que ya venía resuelto.
    anexos.assert_not_awaited()


def _content_hashes_de_los_inserts(mock_conn) -> list[str | None]:
    return [
        llamada.args[4]
        for llamada in mock_conn.fetchrow.await_args_list
        if llamada.args
        and isinstance(llamada.args[0], str)
        and "INSERT INTO expediente_radicados" in llamada.args[0]
    ]


@pytest.mark.asyncio
async def test_en_el_LOTE_cada_vinculo_recibe_la_suya_y_lo_declarado_manda(
    client_with_conn,
) -> None:
    """El lote es el camino de la inclusión masiva —el que de verdad forma un
    expediente— y tiene las dos reglas del individual: se resuelve lo que falta
    y se respeta lo que el llamante declara. Sin este test, un lote que
    ignorara la huella declarada pasaba en verde."""
    import uuid as _uuid

    from tests.conftest import make_expediente_record, make_radicado_link_record
    from tests.test_archive import EXPEDIENTE_ID, TENANT_HEADERS

    client, mock_conn = client_with_conn
    exp = make_expediente_record(exp_id=EXPEDIENTE_ID, exp_status="open")
    r1, r2 = _uuid.uuid4(), _uuid.uuid4()
    mock_conn.fetchrow.side_effect = [
        exp,
        None,
        make_radicado_link_record(radicado_id=r1),
        make_radicado_link_record(radicado_id=r2),
        exp,
        None,
        exp,
    ]
    mock_conn.fetch.return_value = []
    mock_conn.fetchval.side_effect = [True, 0, 1, 0, 2, None, None]

    declarada = "d" * 64
    anexos = AsyncMock(return_value={str(r1): [{"file_id": str(_FILE), "filename": "a.pdf"}]})
    with (
        patch("app.clients.document_client.fetch_anexos_internal", anexos),
        _huellas(return_value={str(_FILE): _HASH}),
    ):
        resp = await client.post(
            f"/api/v1/expedientes/{EXPEDIENTE_ID}/radicados/batch",
            json={
                "radicados": [
                    {"radicado_id": str(r1), "tracking_number": "2024-A-001"},
                    {
                        "radicado_id": str(r2),
                        "tracking_number": "2024-A-002",
                        "content_hash": declarada,
                    },
                ]
            },
            headers=TENANT_HEADERS,
        )

    assert resp.status_code == 200
    assert _content_hashes_de_los_inserts(mock_conn) == [_HASH, declarada]
    # Un solo viaje, y solo por el que no traía huella.
    assert anexos.await_count == 1
    assert anexos.await_args.kwargs["radicado_ids"] == [r1]
