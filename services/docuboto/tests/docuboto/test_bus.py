# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El bucle del bus (`docuboto.core.bus`, ADR-049), sin base.

Lo que es del bucle y no de la tabla: confirmar solo lo que salió bien, que un
fallo no frene al resto del lote, que una entidad sin tablas no tumbe la vuelta,
y que el trabajo periódico de un grupo corra a su cadencia sin parar el consumo
si falla. La tabla de verdad la prueba
`tests/document/integration/test_bus_eventos_integration.py`.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

import asyncpg
import pytest

from docuboto.core import bus

pytestmark = pytest.mark.asyncio


class _Registro:
    def __init__(self, lote: list[tuple[str, dict[str, str]]]) -> None:
        self.lote = lote
        self.confirmados: list[str] = []
        self.fallidos: list[str] = []


@pytest.fixture
def registro(monkeypatch: pytest.MonkeyPatch) -> _Registro:
    reg = _Registro([("e-1", {"n": "1"}), ("e-2", {"n": "2"}), ("e-3", {"n": "3"})])

    @asynccontextmanager
    async def _acquire(_slug: str):
        yield object()

    async def _leer(_conn: Any, _group: str, _streams: Any, *, limite: int) -> list:
        lote, reg.lote = reg.lote[:limite], reg.lote[limite:]
        return lote

    async def _ack(_conn: Any, _group: str, event_id: str) -> None:
        reg.confirmados.append(event_id)

    async def _fallo(_conn: Any, _group: str, event_id: str, _exc: BaseException) -> None:
        reg.fallidos.append(event_id)

    monkeypatch.setattr(bus, "acquire_tenant", _acquire)
    monkeypatch.setattr(bus.events, "leer", _leer)
    monkeypatch.setattr(bus.events, "ack", _ack)
    monkeypatch.setattr(bus.events, "record_failure", _fallo)
    return reg


async def test_confirma_lo_que_sale_bien_y_un_fallo_no_frena_al_lote(registro: _Registro) -> None:
    async def handler(campos: dict[str, str]) -> None:
        if campos["n"] == "2":
            raise RuntimeError("se rompe")

    tomados = await bus.procesar_tenant("demo", group="g", streams=("s",), handler=handler)

    assert tomados == 3
    assert registro.confirmados == ["e-1", "e-3"]
    assert registro.fallidos == ["e-2"], "el fallo queda pendiente con su error, sin confirmar"


async def test_la_cancelacion_no_se_traga(registro: _Registro) -> None:
    async def handler(_campos: dict[str, str]) -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await bus.procesar_tenant("demo", group="g", streams=("s",), handler=handler)
    assert registro.confirmados == [] and registro.fallidos == []


async def test_una_entidad_sin_tablas_no_tumba_la_vuelta(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _tenants(_pool: Any) -> list[str]:
        return ["vieja", "nueva"]

    monkeypatch.setattr(bus, "tenants_activos", _tenants)
    monkeypatch.setattr(bus, "get_pool", lambda: None)

    async def accion(slug: str) -> str:
        if slug == "vieja":
            raise asyncpg.UndefinedTableError("eventos")
        return slug

    assert await bus._por_tenant(accion, "g") == ["nueva"]


async def test_el_trabajo_periodico_corre_y_su_fallo_no_para_el_consumo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vueltas = 0

    async def _por_tenant(_accion: Any, _group: str) -> list[int]:
        nonlocal vueltas
        vueltas += 1
        if vueltas >= 4:
            raise asyncio.CancelledError
        return [0]

    llamadas = 0

    async def periodico() -> None:
        nonlocal llamadas
        llamadas += 1
        raise RuntimeError("la retención falló")

    monkeypatch.setattr(bus, "_por_tenant", _por_tenant)
    monkeypatch.setattr(bus, "_suscribir", lambda: asyncio.Event())
    monkeypatch.setattr(bus, "_desuscribir", lambda _e: None)
    monkeypatch.setattr(bus, "ESPERA", 0.0)
    monkeypatch.setattr(bus, "RECLAIM_INTERVAL", 10_000.0)

    with pytest.raises(asyncio.CancelledError):
        await bus.consumir(
            group="g",
            streams=("s",),
            handler=lambda _c: None,  # type: ignore[arg-type, return-value]
            periodico=periodico,
            intervalo_periodico=0.0,
        )

    assert vueltas == 4, "el consumo siguió tras cada fallo del trabajo periódico"
    assert llamadas == 3
