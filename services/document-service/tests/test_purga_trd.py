# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La purga de radicados por disposición final — E04, RF-TRD-07 fase 5.

El único camino de document-service que destruye. Lo que se prueba:

* **Los bytes van ANTES que las filas.** Al revés quedarían objetos huérfanos en
  MinIO que nadie sabe ya a qué radicado pertenecían: imposibles de encontrar y,
  por tanto, imposibles de eliminar — el peor desenlace para una obligación de
  destruir.
* **Si los bytes no se van, NO se borra ninguna fila.** El error sube como 502 y
  no se degrada: los documentos siguen existiendo.
* **El asiento va en la misma transacción que el borrado**, con el número de
  radicado: es lo único que quedará de ese documento.
* **El gate es `X-Internal-Token`**: nadie de fuera puede pedir una destrucción.
"""

import contextlib
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.clients.storage_client import StoragePurgaError
from app.services.purga_service import PurgaService
from tests.conftest import _make_mock_conn


@pytest.fixture
def mock_conn():
    """Conexión doble. Se construye aquí y no vía `client_with_conn` porque
    estos tests ejercen el SERVICIO directamente: el endpoint solo lo envuelve,
    y montar la app entera escondería el orden de las llamadas, que es
    justamente lo que hay que observar."""
    return _make_mock_conn()

RAD_A = uuid.uuid4()
RAD_B = uuid.uuid4()
EXP = str(uuid.uuid4())


class _Anexo:
    def __init__(self, file_id):
        self.file_id = file_id


def _servicio(conn, redis=None):
    return PurgaService(conn, "demo", redis=redis)


@pytest.mark.asyncio
async def test_los_bytes_se_borran_ANTES_que_las_filas(mock_conn) -> None:
    orden: list[str] = []
    files = [uuid.uuid4(), uuid.uuid4()]

    async def _purgar(**kwargs):
        orden.append("bytes")
        return kwargs["file_ids"]

    async def _execute(*args, **kwargs):
        if isinstance(args[0], str) and "DELETE FROM radicados" in args[0]:
            orden.append("fila")
        return "DELETE 1"

    mock_conn.execute.side_effect = _execute
    # El doble responde a lo que se le PREGUNTA, no con un valor único para toda
    # consulta. Dejó de valer el día que la purga ganó una segunda consulta —la
    # que averigua qué anexos comparte otro radicado— y le llegaban las filas de
    # la primera, sin la columna que esperaba.
    async def _fetch(sql, *args, **kw):
        if "anexos" in sql.lower() and "file_id" in sql.lower():
            return []  # ningún fichero compartido fuera del lote
        return [{"id": RAD_A, "tracking_number": "2026-DEMO-E-000001"}]

    mock_conn.fetch.side_effect = _fetch

    with (
        patch(
            "app.repositories.document_repository.DocumentRepository."
            "list_anexos_for_radicados_internal",
            AsyncMock(return_value={RAD_A: [_Anexo(files[0]), _Anexo(files[1])]}),
        ),
        patch("app.clients.storage_client.purgar_disposicion", _purgar),
    ):
        recuento = await _servicio(mock_conn).purgar_radicados(
            [RAD_A], expediente_id=EXP, motivo="TRD", actor=uuid.uuid4()
        )

    assert orden == ["bytes", "fila"], orden
    assert recuento == {"radicados_eliminados": 1, "anexos_eliminados": 2}


@pytest.mark.asyncio
async def test_si_los_bytes_NO_se_van_no_se_borra_ninguna_fila(mock_conn) -> None:
    """Un fallo aquí significa que los documentos siguen existiendo."""
    mock_conn.execute.return_value = "DELETE 1"

    with (
        patch(
            "app.repositories.document_repository.DocumentRepository."
            "list_anexos_for_radicados_internal",
            AsyncMock(return_value={RAD_A: [_Anexo(uuid.uuid4())]}),
        ),
        patch(
            "app.clients.storage_client.purgar_disposicion",
            AsyncMock(side_effect=StoragePurgaError("storage inalcanzable")),
        ),
        pytest.raises(StoragePurgaError),
    ):
        await _servicio(mock_conn).purgar_radicados(
            [RAD_A], expediente_id=EXP, motivo="TRD", actor=uuid.uuid4()
        )

    sql = " ".join(str(c.args[0]) for c in mock_conn.execute.call_args_list)
    assert "DELETE FROM radicados" not in sql


@pytest.mark.asyncio
async def test_el_asiento_conserva_el_NUMERO_del_radicado_destruido(mock_conn) -> None:
    """Sin él, el asiento diría que se destruyó un UUID — y el número es lo
    único que queda de ese documento."""
    mock_conn.execute.return_value = "DELETE 1"
    mock_conn.fetch.return_value = [{"id": RAD_A, "tracking_number": "2026-DEMO-E-000042"}]

    with (
        patch(
            "app.repositories.document_repository.DocumentRepository."
            "list_anexos_for_radicados_internal",
            AsyncMock(return_value={}),
        ),
        patch("app.clients.storage_client.purgar_disposicion", AsyncMock(return_value=[])),
    ):
        await _servicio(mock_conn).purgar_radicados(
            [RAD_A], expediente_id=EXP, motivo="TRD", actor=uuid.uuid4()
        )

    asientos = [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str)
        and "INSERT INTO public.audit_log" in c.args[0]
        and c.args[5] == "document.radicado_eliminado_trd"
    ]
    assert asientos
    assert "2026-DEMO-E-000042" in asientos[0][8]


@pytest.mark.asyncio
async def test_los_numeros_se_leen_ANTES_de_borrar(mock_conn) -> None:
    """Después la fila ya no está para preguntárselo."""
    orden: list[str] = []

    async def _fetch(*args, **kwargs):
        if "tracking_number FROM radicados" in str(args[0]):
            orden.append("lee")
        return [{"id": RAD_A, "tracking_number": "2026-DEMO-E-000001"}]

    async def _execute(*args, **kwargs):
        if isinstance(args[0], str) and "DELETE FROM radicados" in args[0]:
            orden.append("borra")
        return "DELETE 1"

    mock_conn.fetch.side_effect = _fetch
    mock_conn.execute.side_effect = _execute

    with (
        patch(
            "app.repositories.document_repository.DocumentRepository."
            "list_anexos_for_radicados_internal",
            AsyncMock(return_value={}),
        ),
        patch("app.clients.storage_client.purgar_disposicion", AsyncMock(return_value=[])),
    ):
        await _servicio(mock_conn).purgar_radicados(
            [RAD_A], expediente_id=EXP, motivo="TRD", actor=uuid.uuid4()
        )

    assert orden == ["lee", "borra"], orden


@pytest.mark.asyncio
async def test_un_radicado_que_ya_no_estaba_no_se_cuenta_ni_se_audita(mock_conn) -> None:
    """Un reintento tras un fallo a medias tiene que converger, y contar como
    eliminado lo que no estaba inflaría la cifra que la lápida afirma."""
    mock_conn.execute.return_value = "DELETE 0"
    mock_conn.fetch.return_value = [{"id": RAD_A, "tracking_number": "2026-DEMO-E-000001"}]

    with (
        patch(
            "app.repositories.document_repository.DocumentRepository."
            "list_anexos_for_radicados_internal",
            AsyncMock(return_value={}),
        ),
        patch("app.clients.storage_client.purgar_disposicion", AsyncMock(return_value=[])),
    ):
        recuento = await _servicio(mock_conn).purgar_radicados(
            [RAD_A], expediente_id=EXP, motivo="TRD", actor=uuid.uuid4()
        )

    assert recuento["radicados_eliminados"] == 0
    acciones = [
        c.args[5]
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert "document.radicado_eliminado_trd" not in acciones


@pytest.mark.asyncio
async def test_no_se_destruyen_los_bytes_que_otro_expediente_sigue_usando(client_with_conn):
    """El almacén deduplica por huella: dos radicados con el mismo documento
    comparten fila de fichero, y no hay contador de referencias en ninguna parte.

    Sin este recorte, disponer un expediente borraba el anexo de OTRO vigente que
    lo referenciaba, y el segundo quedaba con una fila que ya no resuelve y una
    descarga que devuelve el 404 neutro — indistinguible de «no existe», o sea ni
    siquiera diagnosticable.
    """
    _, mock_conn = client_with_conn
    compartido = uuid.uuid4()
    propio = uuid.uuid4()
    pedidos: list[list] = []

    async def _fetch(sql, *args, **kw):
        if "anexos" in sql.lower() and "file_id" in sql.lower():
            return [{"file_id": compartido}]  # otro radicado lo referencia
        return [{"id": RAD_A, "tracking_number": "2026-DEMO-E-000001"}]

    mock_conn.fetch.side_effect = _fetch

    async def _purgar(*, tenant_slug, file_ids, expediente_id, motivo, actor):
        pedidos.append(list(file_ids))
        return list(file_ids)

    with (
        patch(
            "app.repositories.document_repository.DocumentRepository."
            "list_anexos_for_radicados_internal",
            AsyncMock(return_value={RAD_A: [_Anexo(compartido), _Anexo(propio)]}),
        ),
        patch("app.clients.storage_client.purgar_disposicion", _purgar),
    ):
        with contextlib.suppress(Exception):
            await _servicio(mock_conn).purgar_radicados(
                [RAD_A], expediente_id=EXP, motivo="TRD", actor=uuid.uuid4()
            )

    assert pedidos, "no se llegó a pedir la purga: la prueba no mide nada"
    assert propio in pedidos[0], "no se purgó el anexo que sí correspondía"
    assert compartido not in pedidos[0], (
        "se destruyeron los bytes de un anexo que otro expediente vigente referencia"
    )
