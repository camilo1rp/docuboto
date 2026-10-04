# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""AREA-09-05 — el CABLEADO del relé del outbox, no solo la pieza.

`outbox.retransmitir` se prueba con Postgres real en
`tests/integration/test_outbox_rele_integration.py`; aquí se prueba que el barrido
la LLAMA y con el Redis del `lifespan`. Sin esto, un `bucle_de_barrido()` sin
argumento (el estado previo al cambio) deja el relé muerto: `redis is None` →
la fase 4 se salta en silencio y el evento 1→3 perdido no se reenvía jamás, con
toda la suite en verde.

Mutaciones verificadas:
- `bucle_de_barrido(app.state.redis)` → `bucle_de_barrido()` en `main.py`: cae
  `test_el_lifespan_entrega_el_redis_al_bucle_de_barrido`;
- quitar `redis` de `barrer_tenant(slug, redis)` en `barrer_una_vez`: cae
  `test_la_pasada_entrega_el_redis_a_cada_tenant`;
- quitar la fase 4 de `barrer_tenant`: cae `test_la_fase_4_reenvia_con_el_redis_y_lo_informa`.
"""

import inspect
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from app import main as app_main
from app.services import barrido


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
    return conn


@pytest.mark.asyncio
async def test_la_fase_4_reenvia_con_el_redis_y_lo_informa(monkeypatch) -> None:
    conn = _fases_inertes(monkeypatch)
    rele = AsyncMock(return_value=3)
    monkeypatch.setattr(barrido.outbox, "retransmitir", rele)
    redis = object()
    r = await barrido.barrer_tenant("demo", redis)
    assert r["eventos_reenviados"] == 3
    rele.assert_awaited_once_with(conn, redis)


@pytest.mark.asyncio
async def test_sin_redis_la_fase_4_no_se_intenta(monkeypatch) -> None:
    _fases_inertes(monkeypatch)
    rele = AsyncMock(return_value=9)
    monkeypatch.setattr(barrido.outbox, "retransmitir", rele)
    r = await barrido.barrer_tenant("demo", None)
    assert r["eventos_reenviados"] == 0
    rele.assert_not_awaited()


@pytest.mark.asyncio
async def test_un_fallo_del_rele_no_tumba_el_barrido_pero_una_tabla_ausente_si_se_propaga(
    monkeypatch,
) -> None:
    _fases_inertes(monkeypatch)
    monkeypatch.setattr(barrido.outbox, "retransmitir", AsyncMock(side_effect=RuntimeError("redis")))
    r = await barrido.barrer_tenant("demo", object())
    assert r["eventos_reenviados"] == 0
    # Tenant sin migración 042: lo decide `barrer_una_vez` (omitirlo), no se traga aquí.
    monkeypatch.setattr(
        barrido.outbox, "retransmitir", AsyncMock(side_effect=asyncpg.UndefinedTableError("x"))
    )
    with pytest.raises(asyncpg.UndefinedTableError):
        await barrido.barrer_tenant("demo", object())


@pytest.mark.asyncio
async def test_la_pasada_entrega_el_redis_a_cada_tenant(monkeypatch) -> None:
    monkeypatch.setattr(barrido, "get_pool", lambda: MagicMock())
    monkeypatch.setattr(barrido, "tenants_activos", AsyncMock(return_value=["a", "b"]))
    barrer = AsyncMock(return_value={})
    monkeypatch.setattr(barrido, "barrer_tenant", barrer)
    redis = object()
    await barrido.barrer_una_vez(redis)
    assert [c.args for c in barrer.await_args_list] == [("a", redis), ("b", redis)]


@pytest.mark.asyncio
async def test_el_bucle_entrega_el_redis_a_la_pasada(monkeypatch) -> None:
    import asyncio

    monkeypatch.setattr(barrido.settings, "barrido_retraso_inicial_segundos", 0)
    pasada = AsyncMock(side_effect=asyncio.CancelledError)
    monkeypatch.setattr(barrido, "barrer_una_vez", pasada)
    redis = object()
    with pytest.raises(asyncio.CancelledError):
        await barrido.bucle_de_barrido(redis)
    pasada.assert_awaited_once_with(redis)


def test_el_lifespan_entrega_el_redis_al_bucle_de_barrido() -> None:
    """El lifespan arranca el bucle en un `create_task` que ningún test de unidad ejecuta
    (necesita Postgres y Redis): se comprueba el SITIO de la llamada."""
    fuente = inspect.getsource(app_main.lifespan)
    assert "bucle_de_barrido(app.state.redis)" in fuente
