# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""`GET /metrics` de archive-service: la señal `worm` (C18, ADR-041).

Mutaciones que deben caer (descritas, no ejecutadas):

* sumar parcial cuando falla un tenant (tragarse la excepción de una rama) →
  `test_si_falla_un_tenant_no_hay_series_de_valor`;
* no calificar con el schema (`"tenant_x".tabla`) → `test_el_sql_califica_cada_tabla_por_schema`;
* no emitir las combinaciones con 0 → `test_cada_combinacion_se_emite_tambien_con_cero`;
* confundir las fases (reconcile ↔ renewal) → `test_el_conteo_cae_en_su_artefacto_y_fase`.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient
from orpycamcp_common.metricas import ARTEFACTOS_WORM, FASES_WORM

from app.main import app
from app.repositories import metricas_repository as repo
from app.routers import metrics as m


class _Conn:
    def __init__(self, *, tenants: list[str] | None = None, filas: list[Any] | None = None, falla: bool = False) -> None:
        self.tenants = [{"slug": s} for s in (tenants if tenants is not None else ["demo"])]
        self.filas = filas or []
        self.falla = falla
        self.sqls: list[str] = []

    @asynccontextmanager
    async def transaction(self, **kw: Any):  # noqa: ANN202
        yield

    async def execute(self, sql: str, *a: Any) -> str:
        return "SET"

    async def fetch(self, sql: str, *a: Any) -> list[Any]:
        self.sqls.append(sql)
        if "FROM public.tenants" in sql:
            return self.tenants
        if self.falla:
            raise RuntimeError('relation "tenant_demo.expediente_aip" does not exist')
        return self.filas


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


def test_las_combinaciones_caben_en_el_registro_cerrado() -> None:
    assert len(repo.COMBINACIONES) >= 12  # anti-vacuidad
    for artefacto, _tabla, fase, _pred in repo.COMBINACIONES:
        assert artefacto in ARTEFACTOS_WORM and fase in FASES_WORM
    assert {a for a, *_ in repo.COMBINACIONES} == ARTEFACTOS_WORM  # ningún artefacto sin métrica


def test_el_sql_califica_cada_tabla_por_schema() -> None:
    sql = repo.sql_agotados(["tenant_a", "tenant_b_c"])
    assert sql.count('"tenant_a".') == sql.count('"tenant_b_c".') == len(repo.COMBINACIONES)
    assert "GROUP BY artifact, phase" in sql and "UNION ALL" in sql
    assert "estado = 'agotado'" in sql  # el empaquetado del AIP


def test_un_schema_dudoso_no_se_interpola() -> None:
    with pytest.raises(ValueError):
        repo.sql_agotados(['tenant_x"; DROP TABLE y; --'])


async def test_cada_combinacion_se_emite_tambien_con_cero() -> None:
    conn = _Conn(filas=[])
    r = await _get(conn)
    assert r.status_code == 200
    assert 'orpycamcp_signal_measured{signal="worm"} 1' in r.text
    lineas = [x for x in r.text.splitlines() if x.startswith("orpycamcp_worm_exhausted_artifacts{")]
    assert len(lineas) == len(repo.COMBINACIONES)
    assert all(x.endswith(" 0") for x in lineas)  # medido y limpio: 0 es verdad
    assert not any("tenant" in x and "tenant_" in x for x in lineas)  # sin etiqueta de tenant


async def test_el_conteo_cae_en_su_artefacto_y_fase() -> None:
    filas = [
        {"artifact": "expediente_aip", "phase": "packaging", "n": 2},
        {"artifact": "expediente_indice", "phase": "renewal", "n": 1},
    ]
    r = await _get(_Conn(filas=filas))
    assert 'orpycamcp_worm_exhausted_artifacts{artifact="expediente_aip",phase="packaging"} 2' in r.text
    assert 'orpycamcp_worm_exhausted_artifacts{artifact="expediente_indice",phase="renewal"} 1' in r.text
    assert 'orpycamcp_worm_exhausted_artifacts{artifact="expediente_indice",phase="reconcile"} 0' in r.text


async def test_si_falla_un_tenant_no_hay_series_de_valor() -> None:
    r = await _get(_Conn(falla=True))
    assert r.status_code == 200
    assert 'orpycamcp_signal_measured{signal="worm"} 0' in r.text
    assert "orpycamcp_worm_exhausted_artifacts" not in r.text


async def test_una_fila_ajena_al_registro_no_aparece_en_la_salida() -> None:
    r = await _get(_Conn(filas=[{"artifact": "inventado", "phase": "renewal", "n": 1}]))
    # la salida se arma recorriendo COMBINACIONES, no lo que devuelva la base
    assert 'orpycamcp_signal_measured{signal="worm"} 1' in r.text
    assert "inventado" not in r.text


async def test_sin_tenants_se_mide_todo_en_cero() -> None:
    conn = _Conn(tenants=[])
    r = await _get(conn)
    assert 'orpycamcp_signal_measured{signal="worm"} 1' in r.text
    assert not any("tenant_" in s for s in conn.sqls)  # no consulta ningún schema


async def test_con_cabeceras_de_proxy_es_404() -> None:
    conn = _Conn()
    r = await _get(conn, **{"X-Forwarded-For": "1.2.3.4"})
    assert r.status_code == 404 and conn.sqls == []


async def test_main_monta_metrics_fuera_del_openapi() -> None:
    # FastAPI pospone la inclusión (`app.routes` no expone `.path`): se prueba pidiéndola.
    assert (await _get(_Conn())).status_code == 200
    assert "/metrics" not in app.openapi()["paths"]
