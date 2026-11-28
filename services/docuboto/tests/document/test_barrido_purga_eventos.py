# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""ADR-049 — el CABLEADO de la caducidad de los eventos del bus y de los dead-letters.

`events.purgar` se prueba contra Postgres real en
`integration/test_bus_eventos_integration.py`; aquí, que el barrido la LLAMA en cada
entidad con la retención configurada, que lo informa, y que un fallo de la purga no
frena las fases siguientes. Sin esto la tabla `eventos` crecería para siempre con la
suite en verde.

Mutaciones verificadas:
- quitar la fase 4 de `barrer_tenant` → cae `test_la_fase_4_purga_con_la_retencion_y_lo_informa`;
- dejar que su excepción suba → cae `test_un_fallo_de_la_purga_no_frena_el_resto`.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import pytest

from document.services import barrido


def _acquire(conn):
    @asynccontextmanager
    async def _a(slug):
        yield conn

    return _a


def _fases_inertes(monkeypatch) -> AsyncMock:
    conn = AsyncMock()
    conn.fetchval.return_value = False  # otra réplica tiene la purga: no estorba
    monkeypatch.setattr(barrido, "acquire_tenant", _acquire(conn))
    monkeypatch.setattr(barrido.BatchService, "reanudar_interrumpidos", AsyncMock(return_value=0))
    monkeypatch.setattr(barrido.BatchService, "purgar_lotes_antiguos", AsyncMock(return_value=0))
    monkeypatch.setattr(barrido, "drenar_callbacks_diferidos", AsyncMock(return_value=0))
    monkeypatch.setattr(barrido.events, "purgar", AsyncMock(return_value=0))
    monkeypatch.setattr(barrido.dead_letter, "purgar_terminales", AsyncMock(return_value=0))
    return conn


@pytest.mark.asyncio
async def test_la_fase_4_purga_con_la_retencion_y_lo_informa(monkeypatch) -> None:
    conn = _fases_inertes(monkeypatch)
    purgar = AsyncMock(return_value=7)
    monkeypatch.setattr(barrido.events, "purgar", purgar)
    monkeypatch.setattr(barrido.settings, "eventos_retencion_dias", 12)

    resumen = await barrido.barrer_tenant("demo")

    purgar.assert_awaited_once_with(conn, dias=12)
    assert resumen["eventos_purgados"] == 7


@pytest.mark.asyncio
async def test_un_fallo_de_la_purga_no_frena_el_resto(monkeypatch) -> None:
    _fases_inertes(monkeypatch)
    monkeypatch.setattr(barrido.events, "purgar", AsyncMock(side_effect=RuntimeError("x")))
    drenar = AsyncMock(return_value=2)
    monkeypatch.setattr(barrido, "drenar_callbacks_diferidos", drenar)

    resumen = await barrido.barrer_tenant("demo")

    assert resumen["eventos_purgados"] == 0
    assert resumen["callbacks_diferidos"] == 2


@pytest.mark.asyncio
async def test_la_pasada_recorre_cada_entidad(monkeypatch) -> None:
    monkeypatch.setattr(barrido, "tenants_activos", AsyncMock(return_value=["a", "b"]))
    monkeypatch.setattr(barrido, "get_pool", lambda: None)
    barrer = AsyncMock(return_value={"eventos_purgados": 0})
    monkeypatch.setattr(barrido, "barrer_tenant", barrer)

    resumen = await barrido.barrer_una_vez()

    assert [c.args for c in barrer.await_args_list] == [("a",), ("b",)]
    assert set(resumen) == {"a", "b"}


@pytest.mark.asyncio
async def test_la_fase_4_purga_los_dead_letters_terminales_y_lo_informa(monkeypatch) -> None:
    conn = _fases_inertes(monkeypatch)
    purgar = AsyncMock(return_value=3)
    monkeypatch.setattr(barrido.dead_letter, "purgar_terminales", purgar)
    monkeypatch.setattr(barrido.settings, "deadletter_retencion_dias", 45)

    resumen = await barrido.barrer_tenant("demo")

    purgar.assert_awaited_once_with(conn, "demo", dias=45)
    assert resumen["dead_letters_purgados"] == 3


@pytest.mark.asyncio
async def test_un_fallo_de_la_purga_de_dead_letters_no_frena_el_resto(monkeypatch) -> None:
    _fases_inertes(monkeypatch)
    monkeypatch.setattr(
        barrido.dead_letter, "purgar_terminales", AsyncMock(side_effect=RuntimeError("x"))
    )
    drenar = AsyncMock(return_value=1)
    monkeypatch.setattr(barrido, "drenar_callbacks_diferidos", drenar)

    resumen = await barrido.barrer_tenant("demo")

    assert resumen["dead_letters_purgados"] == 0
    assert resumen["callbacks_diferidos"] == 1
