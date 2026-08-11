# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Supervisión de los consumidores de eventos.

Los cuatro consumidores del sistema se lanzaban con `asyncio.create_task(run())`
sin `done_callback` ni supervisor. `run()` llama a `ensure_group` **antes** de
entrar en su bucle y fuera de todo `try`: si Redis no está disponible en ese
instante —arranque en frío, o un reinicio de Redis— la excepción mata la tarea.
El proceso sigue vivo, la API responde, `/health` devuelve 200 y **nadie consume
nada**: en workflow eso es la correspondencia entrante que deja de repartirse.

El test que importa es el primero: comprueba que el consumidor **vuelve** tras
morir. Los demás acotan la forma de esa vuelta.
"""

import asyncio
import logging

import pytest
from orpycamcp_common import events


@pytest.fixture(autouse=True)
def _limpiar_estado():
    events._estado_consumidores.clear()
    yield
    events._estado_consumidores.clear()


async def test_el_consumidor_muerto_vuelve() -> None:
    """Redis caído al arrancar mataba la tarea PARA SIEMPRE. Ahora reintenta."""
    intentos = []
    listo = asyncio.Event()

    async def consumidor() -> None:
        intentos.append(1)
        if len(intentos) < 3:
            raise ConnectionError("Redis no disponible")
        listo.set()
        await asyncio.sleep(3600)  # el bucle real, que ya no muere

    tarea = events.spawn_supervised(
        consumidor, name="prueba", backoff_inicial=0.001, backoff_maximo=0.001
    )
    await asyncio.wait_for(listo.wait(), timeout=2)
    tarea.cancel()

    assert len(intentos) == 3, "debía reintentar hasta que el consumidor arrancó"


async def test_reintenta_tambien_si_el_consumidor_TERMINA_solo() -> None:
    """Un consumidor que devuelve sin excepción también es un fallo.

    El bucle de consumo es `while True`: que retorne significa que alguien lo
    rompió. Salir en silencio dejaría el sistema igual de mudo que una excepción
    tragada.
    """
    intentos = []

    async def consumidor() -> None:
        intentos.append(1)
        return

    tarea = events.spawn_supervised(
        consumidor, name="prueba", backoff_inicial=0.001, backoff_maximo=0.001
    )
    await asyncio.sleep(0.05)
    tarea.cancel()

    assert len(intentos) > 1


async def test_la_cancelacion_no_reintenta() -> None:
    """El apagado del `lifespan` es la única salida legítima del bucle."""
    arrancado = asyncio.Event()

    async def consumidor() -> None:
        arrancado.set()
        await asyncio.sleep(3600)

    tarea = events.spawn_supervised(consumidor, name="prueba", backoff_inicial=0.001)
    await asyncio.wait_for(arrancado.wait(), timeout=2)
    tarea.cancel()
    with pytest.raises(asyncio.CancelledError):
        await tarea

    assert events.consumer_status()["prueba"]["corriendo"] is False


async def test_la_muerte_queda_en_el_log(caplog) -> None:
    """Sin esto el fallo es MUDO: asyncio solo lo delata al recolectar la tarea."""

    async def consumidor() -> None:
        raise ConnectionError("Redis no disponible")

    with caplog.at_level(logging.ERROR):
        tarea = events.spawn_supervised(
            consumidor,
            name="prueba",
            logger=logging.getLogger("prueba.supervisor"),
            backoff_inicial=0.001,
            backoff_maximo=0.001,
        )
        await asyncio.sleep(0.02)
        tarea.cancel()

    assert any("murió" in r.message for r in caplog.records)
    assert any(r.exc_info for r in caplog.records), "debe registrar la excepción, no solo el texto"


async def test_la_espera_crece_para_no_martillear_un_redis_caido() -> None:
    """Reintentar sin pausa contra un Redis caído es parte del incidente."""
    esperas = []
    dormir_real = asyncio.sleep

    async def dormir_espia(segundos, *args, **kwargs):
        esperas.append(segundos)
        return await dormir_real(0, *args, **kwargs)

    async def consumidor() -> None:
        raise ConnectionError("caído")

    original = asyncio.sleep
    asyncio.sleep = dormir_espia  # type: ignore[assignment]
    try:
        tarea = events.spawn_supervised(
            consumidor, name="prueba", backoff_inicial=1.0, backoff_maximo=8.0
        )
        await dormir_real(0.05)
        tarea.cancel()
    finally:
        asyncio.sleep = original  # type: ignore[assignment]

    assert esperas[:4] == [1.0, 2.0, 4.0, 8.0]
    assert all(e <= 8.0 for e in esperas), "la espera está topada"


async def test_el_estado_del_consumidor_es_consultable() -> None:
    """Es lo que permite a `/health/ready` decir algo verdadero sobre él."""
    arrancado = asyncio.Event()

    async def consumidor() -> None:
        arrancado.set()
        await asyncio.sleep(3600)

    tarea = events.spawn_supervised(consumidor, name="workflow", backoff_inicial=0.001)
    await asyncio.wait_for(arrancado.wait(), timeout=2)

    estado = events.consumer_status()["workflow"]
    assert estado == {"corriendo": True, "reinicios": 0, "ultimo_error": None}

    tarea.cancel()


async def test_el_estado_recuerda_el_ultimo_error_y_cuenta_reinicios() -> None:
    """`reinicios` creciendo con `corriendo=True` es un consumidor que rebota."""

    async def consumidor() -> None:
        raise ConnectionError("Redis no disponible")

    tarea = events.spawn_supervised(
        consumidor, name="prueba", backoff_inicial=0.001, backoff_maximo=0.001
    )
    await asyncio.sleep(0.03)
    tarea.cancel()

    estado = events.consumer_status()["prueba"]
    assert estado["reinicios"] >= 1
    assert "ConnectionError" in estado["ultimo_error"]
