# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El borrado físico por `storage.api.purge_for_disposition` — E04, RF-TRD-07 fase 5.

Solo lo alcanza document desde dentro del proceso (no hay ruta HTTP). Lo que se prueba:

* **El candado WORM aborta la purga ENTERA del expediente**, no solo la del
  archivo protegido: eliminar el resto dejaría un expediente medio destruido.
* **`no_encontrados` no es un error**: un archivo ya purgado es el estado que se
  buscaba, y un reintento tras un fallo a medias tiene que converger.
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from storage.repositories.file_repository import CitaConstancia
from storage.services.storage_service import AnexoCitadoPorConstanciaError, WormLockActiveError
from storage import api as storage_api
from tests.storage.conftest import TENANT_SLUG

EXP = str(uuid.uuid4())


@pytest.fixture(autouse=True)
def _sin_precheck_de_citas(request):
    """Los tests de esta puerta aíslan `purge_for_disposition`; el pre-chequeo de citas (C-5/C14)
    se prueba aparte, con su propia prueba, y aquí se neutraliza (el doble de conexión no tiene la
    vista contrato, y la vista ausente es fail-closed)."""
    if request.node.get_closest_marker("con_precheck_real"):
        yield
        return
    with patch("storage.services.storage_service.StorageService.verificar_sin_citas", AsyncMock()):
        yield


def _purge(file_ids):
    return storage_api.purge_for_disposition(
        tenant_slug=TENANT_SLUG,
        file_ids=list(file_ids),
        expediente_id=EXP,
        motivo="Eliminación TRD, lote LOTE-2026-001",
        actor=uuid.uuid4(),
    )


@pytest.mark.asyncio
async def test_purga_y_distingue_lo_purgado_de_lo_que_ya_no_estaba(client) -> None:
    """Se devuelven los ids purgados y no un contador: quien orquesta escribe una
    lápida con lo que de verdad ocurrió, y «12 de 14» sin decir cuáles no
    permite afirmar nada."""
    a, b = uuid.uuid4(), uuid.uuid4()

    async def _purgar(self, file_id, *args, **kwargs):
        return file_id == a

    with patch("storage.services.storage_service.StorageService.purge_for_disposition", _purgar):
        purgados = await _purge([a, b])

    # Lo no encontrado ya no estaba: no cuenta como purgado.
    assert purgados == [a]


@pytest.mark.asyncio
async def test_el_candado_WORM_aborta_la_purga_ENTERA(client) -> None:
    """No se purga «lo que se pueda»: un expediente medio destruido, con un
    índice que ya no describe nada, es peor que no haber empezado."""
    a, b = uuid.uuid4(), uuid.uuid4()
    llamadas = {"n": 0}

    async def _purgar(self, file_id, *args, **kwargs):
        llamadas["n"] += 1
        raise WormLockActiveError("retain_until=2030-01-01")

    with patch("storage.services.storage_service.StorageService.purge_for_disposition", _purgar):
        with pytest.raises(storage_api.StoragePurgeError) as exc:
            await _purge([a, b])

    assert isinstance(exc.value.__cause__, WormLockActiveError)
    # Se detuvo en el primero: no siguió intentando con el resto.
    assert llamadas["n"] == 1


@pytest.mark.asyncio
async def test_el_expediente_viaja_porque_es_la_llave_del_gate_WORM(client) -> None:
    """No es un dato decorativo: el gate se evalúa por expediente, no por
    archivo."""
    a = uuid.uuid4()
    recibido = {}

    async def _purgar(self, file_id, expediente_id, motivo, actor=None):
        recibido.update(expediente_id=expediente_id, motivo=motivo)
        return True

    with patch("storage.services.storage_service.StorageService.purge_for_disposition", _purgar):
        await _purge([a])

    assert recibido["expediente_id"] == EXP
    assert "LOTE-2026-001" in recibido["motivo"]


# -- C-5/C14: anexo citado por una constancia de firma inobtenible vigente -----------------


@pytest.mark.asyncio
@pytest.mark.con_precheck_real
async def test_un_archivo_citado_aborta_la_purga_ANTES_de_borrar_ninguno(client_with_conn) -> None:
    """Con tres archivos y el SEGUNDO citado, no se borra ni el primero: la purga corre archivo por
    archivo y un rechazo a mitad dejaría el expediente medio destruido con la constancia citando un
    anexo que ya no existe.

    Mutación que debe caer: quitar la llamada a `verificar_sin_citas` de `purge_for_disposition` -> el primero se
    purga (`llamadas["n"] == 1`) y el rechazo llega tarde."""
    from tests.storage.conftest import vista_de_citas

    _client, mock_conn, _ = client_with_conn
    a, b, c = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    excepcion = uuid.uuid4()
    vista_de_citas(
        mock_conn,
        citas=(
            {
                "excepcion_id": excepcion,
                "transferencia_id": uuid.uuid4(),
                "rol": "recibe",
                "ordinal": 2,
            },
        ),
    )
    # La vista devuelve la cita para CUALQUIER file_id consultado; el 1.º ya basta para abortar.
    llamadas = {"n": 0}

    async def _purgar(self, file_id, *args, **kwargs):
        llamadas["n"] += 1
        return True

    with patch("storage.services.storage_service.StorageService.purge_for_disposition", _purgar):
        with pytest.raises(storage_api.StoragePurgeError) as exc:
            await _purge([a, b, c])

    causa = exc.value.__cause__
    assert isinstance(causa, AnexoCitadoPorConstanciaError)
    assert causa.citas[0].como_dict()["excepcion_id"] == str(excepcion)
    assert llamadas["n"] == 0


@pytest.mark.asyncio
@pytest.mark.con_precheck_real
async def test_sin_citas_el_precheck_deja_pasar_y_se_purga(client_with_conn) -> None:
    """Control positivo del anterior: con la vista presente y vacía, la purga procede."""
    from tests.storage.conftest import vista_de_citas

    _client, mock_conn, _ = client_with_conn
    vista_de_citas(mock_conn, citas=())
    a = uuid.uuid4()

    async def _purgar(self, file_id, *args, **kwargs):
        return True

    with patch("storage.services.storage_service.StorageService.purge_for_disposition", _purgar):
        assert await _purge([a]) == [a]


@pytest.mark.asyncio
@pytest.mark.con_precheck_real
async def test_vista_ausente_bloquea_la_purga_fail_closed(client_with_conn) -> None:
    """Sin la vista contrato (archive no migró) NO se sabe si hay constancias: se conserva todo.
    Mutación: tratar `to_regclass` NULL como «sin citas» -> este test cae."""
    _client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = None  # to_regclass(...) -> NULL
    with patch(
        "storage.services.storage_service.StorageService.purge_for_disposition", AsyncMock()
    ) as purgar:
        with pytest.raises(storage_api.StoragePurgeError) as exc:
            await _purge([uuid.uuid4()])

    causa = exc.value.__cause__
    assert isinstance(causa, AnexoCitadoPorConstanciaError)
    assert [c.como_dict() for c in causa.citas] == [
        {"vista": "v_anexo_citado_vigente", "ausente": True}
    ]
    purgar.assert_not_awaited()


@pytest.mark.asyncio
async def test_la_carrera_residual_cae_en_el_409_del_bloqueo_de_fila(client) -> None:
    """Si el pre-chequeo no vio la cita (se declaró después) y la ve el chequeo bajo `FOR UPDATE`
    de `purge_for_disposition`, el rechazo es el mismo y nombra la constancia."""
    cita = CitaConstancia(uuid.uuid4(), uuid.uuid4(), "elabora", 1)

    async def _purgar(self, file_id, *args, **kwargs):
        raise AnexoCitadoPorConstanciaError(file_id, [cita])

    with patch("storage.services.storage_service.StorageService.purge_for_disposition", _purgar):
        with pytest.raises(storage_api.StoragePurgeError) as exc:
            await _purge([uuid.uuid4()])

    causa = exc.value.__cause__
    assert isinstance(causa, AnexoCitadoPorConstanciaError)
    assert causa.citas[0].como_dict()["excepcion_id"] == str(cita.excepcion_id)
