# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`GET /metrics` de auth-service: la señal `audit_chain` (C18, ADR-041).

El exportador NO verifica la cadena: lee la última corrida COMPLETA de
`public.audit_chain_runs`. Mutaciones que deben caer (descritas, no ejecutadas):

* quitar `alcance = 'completo'` del SELECT (una corrida parcial contaría) →
  `test_solo_cuenta_la_ultima_corrida_completa`;
* emitir ceros cuando no hay corrida completa → `test_sin_corrida_completa_no_hay_series_de_valor`;
* sacar `include_router(metrics_router)` de `main.py` → `test_main_monta_metrics_fuera_del_openapi`.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import app
from app.routers import metrics as m

_FIN = datetime(2026, 10, 20, 3, 0, tzinfo=UTC)


class _Conn:
    def __init__(self, fila: Any) -> None:
        self.fila = fila
        self.sqls: list[str] = []

    @asynccontextmanager
    async def transaction(self, **kw: Any):  # noqa: ANN202
        yield

    async def execute(self, sql: str, *a: Any) -> str:
        self.sqls.append(sql)
        return "SET"

    async def fetchrow(self, sql: str, *a: Any) -> Any:
        self.sqls.append(sql)
        return self.fila


class _Pool:
    def __init__(self, fila: Any) -> None:
        self.conn = _Conn(fila)

    @asynccontextmanager
    async def acquire(self):  # noqa: ANN202
        yield self.conn


@pytest.fixture(autouse=True)
def _cache_limpia():  # noqa: ANN202
    m._SENALES[0]._cache = None
    yield
    m._SENALES[0]._cache = None


async def _get(pool: _Pool, **cabeceras: str):  # noqa: ANN202
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        with patch.object(m, "get_pool", return_value=pool):
            return await c.get("/metrics", headers=cabeceras)


# Forma REAL de la fila: las cuatro columnas que pide el SELECT.
def _fila(**kw: Any) -> dict[str, Any]:
    base = {"finished_at": _FIN, "roturas_nuevas": 0, "roturas_declaradas": 2, "codigo_salida": 0}
    return {**base, **kw}


async def test_la_ultima_corrida_completa_se_publica() -> None:
    r = await _get(_Pool(_fila()))
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert 'orpycamcp_signal_measured{signal="audit_chain"} 1' in r.text
    assert "orpycamcp_audit_chain_breaks 0\n" in r.text  # 0 medido: es verdad
    assert "orpycamcp_audit_chain_declared_breaks 2\n" in r.text
    assert "orpycamcp_audit_chain_verification_ok 1\n" in r.text
    assert f"orpycamcp_audit_chain_last_verification_timestamp_seconds {int(_FIN.timestamp())}" in r.text


async def test_una_rotura_nueva_y_salida_distinta_de_cero() -> None:
    r = await _get(_Pool(_fila(roturas_nuevas=3, codigo_salida=1)))
    assert "orpycamcp_audit_chain_breaks 3\n" in r.text
    assert "orpycamcp_audit_chain_verification_ok 0\n" in r.text


async def test_sin_corrida_completa_no_hay_series_de_valor() -> None:
    r = await _get(_Pool(None))
    assert 'orpycamcp_signal_measured{signal="audit_chain"} 0' in r.text
    assert "orpycamcp_audit_chain_breaks" not in r.text  # ausencia, no un 0 tranquilizador
    assert "orpycamcp_audit_chain_verification_ok" not in r.text


async def test_solo_cuenta_la_ultima_corrida_completa() -> None:
    pool = _Pool(_fila())
    await _get(pool)
    select = next(s for s in pool.conn.sqls if "audit_chain_runs" in s)
    assert "alcance = 'completo'" in select and "ORDER BY id DESC LIMIT 1" in select
    assert "verify_chain" not in select  # nunca verifica en el scrape


async def test_con_cabeceras_de_proxy_es_404_y_no_toca_la_base() -> None:
    pool = _Pool(_fila())
    for cab in ({"X-Forwarded-For": "1.2.3.4"}, {"Forwarded": "for=1.2.3.4"}, {"X-Real-IP": "1.2.3.4"}):
        r = await _get(pool, **cab)
        assert r.status_code == 404 and "orpycamcp" not in r.text
    assert pool.conn.sqls == []


async def test_main_monta_metrics_fuera_del_openapi() -> None:
    # FastAPI pospone la inclusión: `app.routes` guarda routers incluidos sin `.path`,
    # así que "está montada" se prueba pidiéndola, no listando rutas.
    assert (await _get(_Pool(_fila()))).status_code == 200
    assert "/metrics" not in app.openapi()["paths"]
