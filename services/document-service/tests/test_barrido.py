# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""Barrido de fondo: aislamiento entre tenants y entre fases (AREA-09-14/15).

Un tenant (o una fase) que falla se registra y NO frena a los demás; la siguiente
pasada lo reintenta. La lógica de cada fase se prueba con Postgres real en
`tests/integration/test_import_huerfanos_integration.py` y
`test_lote_reanudable_integration.py`.
"""

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from app.services import barrido


def _conn_con_lock(lock: bool = True) -> AsyncMock:
    conn = AsyncMock()
    conn.fetchval.return_value = lock
    return conn


def _acquire(conn):
    @asynccontextmanager
    async def _a(slug):
        yield conn

    return _a


@pytest.mark.asyncio
async def test_un_tenant_que_falla_no_frena_a_los_demas(monkeypatch) -> None:
    """Mutación: quitar el `except` de `barrer_una_vez` → el primer fallo aborta la pasada."""
    monkeypatch.setattr(barrido, "get_pool", lambda: MagicMock())
    monkeypatch.setattr(barrido, "tenants_activos", AsyncMock(return_value=["a", "b", "c"]))
    vistos: list[str] = []

    async def _barrer(slug, redis=None):
        vistos.append(slug)
        if slug == "a":
            raise RuntimeError("boom")
        if slug == "b":
            raise asyncpg.UndefinedTableError("sin tablas")
        return {"huerfanos": 0, "lotes_reanudados": 1, "lotes_retirados": 0}

    monkeypatch.setattr(barrido, "barrer_tenant", _barrer)
    resumen = await barrido.barrer_una_vez()
    assert vistos == ["a", "b", "c"] and list(resumen) == ["c"]


@pytest.mark.asyncio
async def test_una_fase_que_falla_no_impide_las_siguientes(monkeypatch) -> None:
    conn = _conn_con_lock()
    monkeypatch.setattr(barrido, "acquire_tenant", _acquire(conn))
    purga = AsyncMock(side_effect=RuntimeError("storage raro"))
    monkeypatch.setattr(barrido.ImportHuerfanosService, "purgar", purga)
    reanuda = AsyncMock(return_value=2)
    retira = AsyncMock(return_value=5)
    monkeypatch.setattr(barrido.BatchService, "reanudar_interrumpidos", reanuda)
    monkeypatch.setattr(barrido.BatchService, "purgar_lotes_antiguos", retira)
    monkeypatch.setattr(barrido, "drenar_callbacks_diferidos", AsyncMock(return_value=0))
    r = await barrido.barrer_tenant("demo")
    assert r == {
        "huerfanos": 0,
        "lotes_reanudados": 2,
        "lotes_retirados": 5,
        "eventos_reenviados": 0,
        "callbacks_diferidos": 0,
    }
    purga.assert_awaited_once()
    reanuda.assert_awaited_once()


@pytest.mark.asyncio
async def test_si_otra_replica_tiene_la_purga_no_se_purga_pero_si_se_reanudan_lotes(monkeypatch) -> None:
    conn = _conn_con_lock(lock=False)
    monkeypatch.setattr(barrido, "acquire_tenant", _acquire(conn))
    purga = AsyncMock()
    monkeypatch.setattr(barrido.ImportHuerfanosService, "purgar", purga)
    monkeypatch.setattr(barrido.BatchService, "reanudar_interrumpidos", AsyncMock(return_value=1))
    monkeypatch.setattr(barrido.BatchService, "purgar_lotes_antiguos", AsyncMock(return_value=0))
    r = await barrido.barrer_tenant("demo")
    purga.assert_not_awaited()
    assert r["lotes_reanudados"] == 1
