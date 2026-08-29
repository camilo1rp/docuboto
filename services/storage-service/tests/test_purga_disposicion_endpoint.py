# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La puerta al borrado físico — E04, RF-TRD-07 fase 5.

`StorageService.purge_for_disposition` existía desde hacía tiempo, con su gate
WORM y su asiento, y **ningún router lo alcanzaba**: una capacidad construida y
no ofrecida, aquí sobre el acto más irreversible del sistema.

Lo que se prueba de la puerta:

* **Sellada con `X-Internal-Token`** y no enrutada por el gateway: nadie de
  fuera puede pedir un borrado físico.
* **El candado WORM aborta la purga ENTERA del expediente**, no solo la del
  archivo protegido: eliminar el resto dejaría un expediente medio destruido.
* **`no_encontrados` no es un error**: un archivo ya purgado es el estado que se
  buscaba, y un reintento tras un fallo a medias tiene que converger.
"""

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app.services.storage_service import WormLockActiveError
from tests.conftest import INTERNAL_HEADERS, TENANT_HEADERS

RUTA = "/api/v1/storage/internal/purgar-disposicion"
EXP = str(uuid.uuid4())


def _cuerpo(file_ids):
    return {
        "file_ids": [str(f) for f in file_ids],
        "expediente_id": EXP,
        "motivo": "Eliminación TRD, lote LOTE-2026-001",
        "actor": str(uuid.uuid4()),
    }


@pytest.mark.asyncio
async def test_sin_token_interno_no_se_purga_nada(client) -> None:
    """El gate va antes que cualquier otra cosa: nadie de fuera puede pedir un
    borrado físico."""
    with patch(
        "app.services.storage_service.StorageService.purge_for_disposition", AsyncMock()
    ) as purgar:
        resp = await client.post(RUTA, json=_cuerpo([uuid.uuid4()]), headers=TENANT_HEADERS)

    assert resp.status_code in (401, 403)
    purgar.assert_not_awaited()


@pytest.mark.asyncio
async def test_purga_y_distingue_lo_purgado_de_lo_que_ya_no_estaba(client) -> None:
    """Se devuelven las dos listas y no un contador: quien orquesta escribe una
    lápida con lo que de verdad ocurrió, y «12 de 14» sin decir cuáles no
    permite afirmar nada."""
    a, b = uuid.uuid4(), uuid.uuid4()

    async def _purgar(self, file_id, *args, **kwargs):
        return file_id == a

    with patch("app.services.storage_service.StorageService.purge_for_disposition", _purgar):
        resp = await client.post(RUTA, json=_cuerpo([a, b]), headers=INTERNAL_HEADERS)

    assert resp.status_code == 200
    cuerpo = resp.json()
    assert cuerpo["purgados"] == [str(a)]
    assert cuerpo["no_encontrados"] == [str(b)]


@pytest.mark.asyncio
async def test_el_candado_WORM_aborta_la_purga_ENTERA(client) -> None:
    """No se purga «lo que se pueda»: un expediente medio destruido, con un
    índice que ya no describe nada, es peor que no haber empezado."""
    a, b = uuid.uuid4(), uuid.uuid4()
    llamadas = {"n": 0}

    async def _purgar(self, file_id, *args, **kwargs):
        llamadas["n"] += 1
        raise WormLockActiveError("retain_until=2030-01-01")

    with patch("app.services.storage_service.StorageService.purge_for_disposition", _purgar):
        resp = await client.post(RUTA, json=_cuerpo([a, b]), headers=INTERNAL_HEADERS)

    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "worm_lock_activo"
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

    with patch("app.services.storage_service.StorageService.purge_for_disposition", _purgar):
        await client.post(RUTA, json=_cuerpo([a]), headers=INTERNAL_HEADERS)

    assert recibido["expediente_id"] == EXP
    assert "LOTE-2026-001" in recibido["motivo"]
