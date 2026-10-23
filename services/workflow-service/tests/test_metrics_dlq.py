# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`GET /metrics` de workflow-service: la señal `dlq` (C18, ADR-041).

Los otros tres servicios con DLQ (signature, notification, knowledge) comparten
`metricas.senal_dlq`; `shared/tests/test_metricas_frontera.py` fija que los cuatro la
tienen y `shared/tests/test_metricas.py` cubre la señal. Aquí se prueba el
CABLEADO de este servicio: grupo propio, ruta montada y fuera del OpenAPI.

Mutaciones que deben caer (descritas, no ejecutadas):

* filtrar por otro `origin_group` (p. ej. el de signature) →
  `test_cuenta_el_grupo_propio_del_servicio`;
* sacar `include_router(metrics_router)` → `test_main_monta_metrics_fuera_del_openapi`.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.constants import DLQ_GROUP
from app.main import app
from app.routers import metrics as m


class _Conn:
    def __init__(self, n: int, mas_antiguo: datetime | None) -> None:
        self.args: list[Any] = []
        self.sqls: list[str] = []
        self._fila = {"n": n, "m": mas_antiguo}

    @asynccontextmanager
    async def transaction(self, **kw: Any):  # noqa: ANN202
        yield

    async def execute(self, sql: str, *a: Any) -> str:
        return "SET"

    async def fetch(self, sql: str, *a: Any) -> list[Any]:
        self.sqls.append(sql)
        return [{"slug": "demo"}]

    async def fetchrow(self, sql: str, *a: Any) -> Any:
        self.sqls.append(sql)
        self.args = list(a)
        return self._fila


class _Pool:
    def __init__(self, conn: _Conn) -> None:
        self.conn = conn

    @asynccontextmanager
    async def acquire(self):  # noqa: ANN202
        yield self.conn


@pytest.fixture(autouse=True)
def _cache_limpia():  # noqa: ANN202
    m._SENALES[0]._cache = None
    yield
    m._SENALES[0]._cache = None


async def _get(conn: _Conn, **cabeceras: str):  # noqa: ANN202
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        with patch.object(m, "get_pool", return_value=_Pool(conn)):
            return await c.get("/metrics", headers=cabeceras)


async def test_cuenta_el_grupo_propio_del_servicio() -> None:
    conn = _Conn(4, datetime(2026, 5, 1, tzinfo=UTC))
    r = await _get(conn)
    assert conn.args == [[DLQ_GROUP]]  # y solo ese: la tabla es compartida con otros tres servicios
    assert "orpycamcp_dlq_pending_events 4\n" in r.text
    assert f"orpycamcp_dlq_oldest_pending_timestamp_seconds {int(datetime(2026, 5, 1, tzinfo=UTC).timestamp())}" in r.text
    assert 'orpycamcp_signal_measured{signal="dlq"} 1' in r.text
    assert "demo" not in r.text and "tenant" not in r.text.replace("tenants", "")  # sin identidad


async def test_sin_pendientes_el_cero_se_publica_y_la_antiguedad_no() -> None:
    r = await _get(_Conn(0, None))
    assert "orpycamcp_dlq_pending_events 0\n" in r.text
    assert "oldest_pending" not in r.text


async def test_con_cabeceras_de_proxy_es_404() -> None:
    conn = _Conn(1, None)
    r = await _get(conn, **{"X-Real-IP": "9.9.9.9"})
    assert r.status_code == 404 and conn.sqls == []


async def test_main_monta_metrics_fuera_del_openapi() -> None:
    # FastAPI pospone la inclusión (`app.routes` no expone `.path`): se prueba pidiéndola.
    assert (await _get(_Conn(1, None))).status_code == 200
    assert "/metrics" not in app.openapi()["paths"]
