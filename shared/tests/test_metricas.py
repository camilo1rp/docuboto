# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Exportador de métricas mínimo (C18, ADR-041): formato, ausencia ≠ 0, TTL, 404 de proxy.

Mutaciones que deben caer (descritas, no ejecutadas):

* `Senal.obtener` emite las muestras (o ceros) cuando la medición falla →
  `test_un_fallo_no_emite_series_de_valor` y `test_un_fallo_posterior_conserva_el_ultimo_exito`.
* `detras_de_proxy` ignora `Forwarded` / `X-Real-IP` / `X-Forwarded-For` →
  `test_toda_cabecera_de_proxy_da_404` (una por cabecera).
* Quitar el TTL (medir en cada petición) → `test_el_ttl_mide_una_sola_vez_por_ventana`.
* Quitar el `wait_for` → `test_un_timeout_es_no_verificado`.
* Quitar el `asyncio.Lock` → `test_scrapes_concurrentes_miden_una_sola_vez`.
* `render` deja de validar contra el registro → `test_render_rechaza_lo_que_no_esta_en_el_registro`.
* `contar_pendientes` sin calificar el schema → `test_contar_pendientes_califica_cada_schema`.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any

import pytest
from starlette.requests import Request

from orpycamcp_common import deadletter, metricas
from orpycamcp_common.metricas import FAMILIAS, Muestra, Senal

# ── dobles con la FORMA real de asyncpg (Record = mapeo; transaction = ctx async) ──────


class _Conn:
    def __init__(self, *, fetch: list[Any] | None = None, fetchrow: Any = None) -> None:
        self.sqls: list[str] = []
        self._fetch = fetch or []
        self._fetchrow = fetchrow

    @asynccontextmanager
    async def transaction(self, **kw: Any):  # noqa: ANN202
        self.sqls.append(f"BEGIN {kw}")
        yield

    async def execute(self, sql: str, *a: Any) -> str:
        self.sqls.append(sql)
        return "SET"

    async def fetch(self, sql: str, *a: Any) -> list[Any]:
        self.sqls.append(sql)
        return self._fetch

    async def fetchrow(self, sql: str, *a: Any) -> Any:
        self.sqls.append(sql)
        return self._fetchrow


class _Pool:
    def __init__(self, conn: _Conn | None = None) -> None:
        self.conn = conn or _Conn()

    @asynccontextmanager
    async def acquire(self):  # noqa: ANN202
        yield self.conn


def _req(**cabeceras: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/metrics",
            "headers": [(k.lower().encode(), v.encode()) for k, v in cabeceras.items()],
        }
    )


_DLQ = frozenset({"orpycamcp_dlq_pending_events", "orpycamcp_dlq_oldest_pending_timestamp_seconds"})


def _senal(medir: Any, **kw: Any) -> Senal:
    kw.setdefault("ttl_s", 30.0)
    return Senal("dlq", medir, familias=_DLQ, **kw)


async def _ok(conn: Any) -> list[Muestra]:
    return [Muestra("orpycamcp_dlq_pending_events", 0)]


# ── formato 0.0.4 ────────────────────────────────────────────────────────────────


def test_render_emite_help_y_type_una_sola_vez_por_familia() -> None:
    texto = metricas.render(
        [
            Muestra("orpycamcp_worm_exhausted_artifacts", 2, {"artifact": "expediente_aip", "phase": "renewal"}),
            Muestra("orpycamcp_worm_exhausted_artifacts", 0, {"artifact": "expediente_indice", "phase": "reconcile"}),
            Muestra("orpycamcp_dlq_pending_events", 7),
        ]
    )
    assert texto.count("# HELP orpycamcp_worm_exhausted_artifacts ") == 1
    assert texto.count("# TYPE orpycamcp_worm_exhausted_artifacts gauge") == 1
    # el orden de etiquetas es el del registro (artifact, phase), no el del llamante
    assert 'orpycamcp_worm_exhausted_artifacts{artifact="expediente_aip",phase="renewal"} 2' in texto
    assert 'orpycamcp_worm_exhausted_artifacts{artifact="expediente_indice",phase="reconcile"} 0' in texto
    assert "orpycamcp_dlq_pending_events 7\n" in texto
    assert texto.endswith("\n")


def test_el_escapado_cubre_barra_comilla_y_salto() -> None:
    assert metricas._escapar('a\\b"c\nd') == 'a\\\\b\\"c\\nd'


def test_numeros_enteros_sin_decimales_y_epoch_con_fraccion() -> None:
    assert metricas._numero(3.0) == "3"
    assert metricas._numero(1730000000.25) == "1730000000.25"


@pytest.mark.parametrize(
    "muestra",
    [
        Muestra("orpycamcp_inventada", 1),
        Muestra("orpycamcp_dlq_pending_events", 1, {"tenant": "x"}),  # etiqueta no declarada
        Muestra("orpycamcp_signal_measured", 1),  # falta la etiqueta declarada
        Muestra("orpycamcp_signal_measured", 1, {"signal": "otra"}),  # valor fuera del conjunto cerrado
        Muestra("orpycamcp_worm_exhausted_artifacts", 1, {"artifact": "x", "phase": "renewal"}),
        Muestra("orpycamcp_dlq_pending_events", float("nan")),
        Muestra("orpycamcp_dlq_pending_events", float("inf")),
        Muestra("orpycamcp_dlq_pending_events", True),
    ],
)
def test_render_rechaza_lo_que_no_esta_en_el_registro(muestra: Muestra) -> None:
    with pytest.raises(metricas.MetricaInvalida):
        metricas.render([muestra])


def test_control_positivo_el_registro_tiene_las_nueve_familias() -> None:
    assert len(FAMILIAS) == 9
    for nombre in FAMILIAS:
        assert nombre.startswith("orpycamcp_")


# ── ausencia ≠ 0 ─────────────────────────────────────────────────────────────────


async def test_una_medicion_completa_emite_el_valor_incluido_el_cero() -> None:
    cuerpo = (await metricas.responder(_req(), _Pool(), [_senal(_ok)])).body.decode()
    assert 'orpycamcp_signal_measured{signal="dlq"} 1' in cuerpo
    assert "orpycamcp_dlq_pending_events 0\n" in cuerpo  # aquí el 0 ES verdad
    assert 'orpycamcp_signal_last_success_timestamp_seconds{signal="dlq"}' in cuerpo


async def test_un_fallo_no_emite_series_de_valor() -> None:
    async def roto(conn: Any) -> list[Muestra]:
        raise RuntimeError("tabla ausente en un tenant")

    cuerpo = (await metricas.responder(_req(), _Pool(), [_senal(roto)])).body.decode()
    assert 'orpycamcp_signal_measured{signal="dlq"} 0' in cuerpo
    assert "orpycamcp_dlq_pending_events" not in cuerpo  # NO un 0 que parezca «sin problemas»
    assert "last_success" not in cuerpo  # nunca hubo éxito


async def test_un_fallo_posterior_conserva_el_ultimo_exito_y_borra_los_valores() -> None:
    estado = {"falla": False}
    reloj = {"t": 0.0}

    async def medir(conn: Any) -> list[Muestra]:
        if estado["falla"]:
            raise RuntimeError("boom")
        return [Muestra("orpycamcp_dlq_pending_events", 5)]

    s = _senal(medir, ttl_s=10, reloj=lambda: reloj["t"], ahora=lambda: 1_700_000_000.0)
    bien = (await metricas.responder(_req(), _Pool(), [s])).body.decode()
    assert "orpycamcp_dlq_pending_events 5" in bien
    estado["falla"] = True
    reloj["t"] = 11.0
    mal = (await metricas.responder(_req(), _Pool(), [s])).body.decode()
    assert 'orpycamcp_signal_measured{signal="dlq"} 0' in mal
    assert "orpycamcp_dlq_pending_events" not in mal
    assert 'last_success_timestamp_seconds{signal="dlq"} 1700000000' in mal


async def test_una_muestra_de_otra_senal_o_fuera_del_registro_es_fallo() -> None:
    async def ajena(conn: Any) -> list[Muestra]:
        return [Muestra("orpycamcp_audit_chain_breaks", 0)]

    async def inventada(conn: Any) -> list[Muestra]:
        return [Muestra("orpycamcp_dlq_pending_events", 1, {"tenant": "x"})]

    for medir in (ajena, inventada):
        res = await _senal(medir).obtener(_Pool())  # type: ignore[arg-type]
        assert res.ok is False and res.muestras == ()


async def test_un_timeout_es_no_verificado() -> None:
    async def lento(conn: Any) -> list[Muestra]:
        await asyncio.sleep(5)
        return []

    res = await _senal(lento, timeout_s=0.05).obtener(_Pool())  # type: ignore[arg-type]
    assert res.ok is False


async def test_la_medicion_corre_en_solo_lectura_con_statement_timeout() -> None:
    pool = _Pool()
    await _senal(_ok, timeout_s=7).obtener(pool)  # type: ignore[arg-type]
    assert any("readonly" in s and "True" in s for s in pool.conn.sqls)
    assert "SET LOCAL statement_timeout = 7000" in pool.conn.sqls


# ── TTL y concurrencia ───────────────────────────────────────────────────────────


async def test_el_ttl_mide_una_sola_vez_por_ventana() -> None:
    n = {"c": 0}
    reloj = {"t": 100.0}

    async def medir(conn: Any) -> list[Muestra]:
        n["c"] += 1
        return [Muestra("orpycamcp_dlq_pending_events", n["c"])]

    s = _senal(medir, ttl_s=30, reloj=lambda: reloj["t"])
    pool = _Pool()
    for _ in range(3):
        await s.obtener(pool)  # type: ignore[arg-type]
    assert n["c"] == 1
    reloj["t"] += 29.9
    await s.obtener(pool)  # type: ignore[arg-type]
    assert n["c"] == 1
    reloj["t"] += 0.2  # ventana vencida
    res = await s.obtener(pool)  # type: ignore[arg-type]
    assert n["c"] == 2 and res.muestras[0].valor == 2


async def test_scrapes_concurrentes_miden_una_sola_vez() -> None:
    n = {"c": 0}

    async def medir(conn: Any) -> list[Muestra]:
        n["c"] += 1
        await asyncio.sleep(0.02)
        return [Muestra("orpycamcp_dlq_pending_events", 1)]

    s = _senal(medir)
    pool = _Pool()
    await asyncio.gather(*(s.obtener(pool) for _ in range(6)))  # type: ignore[arg-type]
    assert n["c"] == 1


async def test_un_fallo_tambien_se_cachea_para_no_martillar_la_base() -> None:
    n = {"c": 0}

    async def roto(conn: Any) -> list[Muestra]:
        n["c"] += 1
        raise RuntimeError("x")

    s = _senal(roto)
    for _ in range(4):
        await s.obtener(_Pool())  # type: ignore[arg-type]
    assert n["c"] == 1


def test_una_senal_o_familia_fuera_del_registro_no_se_construye() -> None:
    with pytest.raises(ValueError):
        Senal("otra", _ok, familias=_DLQ)
    with pytest.raises(ValueError):
        Senal("dlq", _ok, familias=frozenset({"orpycamcp_inventada"}))


def test_ttl_de_ambiente(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("METRICS_TTL_SECONDS", raising=False)
    assert metricas.ttl_ambiente() == 30.0
    monkeypatch.setenv("METRICS_TTL_SECONDS", "5")
    assert metricas.ttl_ambiente() == 5.0
    for malo in ("abc", "-1"):
        monkeypatch.setenv("METRICS_TTL_SECONDS", malo)
        assert metricas.ttl_ambiente() == 30.0


# ── 404 ante cabeceras de proxy ──────────────────────────────────────────────────


@pytest.mark.parametrize(
    "cabecera,valor",
    [
        ("X-Forwarded-For", "203.0.113.9"),
        ("Forwarded", "for=203.0.113.9"),
        ("X-Real-IP", "203.0.113.9"),
    ],
)
async def test_toda_cabecera_de_proxy_da_404(cabecera: str, valor: str) -> None:
    llamadas = {"c": 0}

    async def medir(conn: Any) -> list[Muestra]:
        llamadas["c"] += 1
        return [Muestra("orpycamcp_dlq_pending_events", 1)]

    resp = await metricas.responder(_req(**{cabecera: valor}), _Pool(), [_senal(medir)])
    assert resp.status_code == 404
    assert b"orpycamcp" not in resp.body  # no publica nada
    assert llamadas["c"] == 0  # ni siquiera mide
    # control positivo: sin la cabecera, la misma señal responde 200
    ok = await metricas.responder(_req(), _Pool(), [_senal(medir)])
    assert ok.status_code == 200


async def test_el_tipo_de_contenido_es_el_de_prometheus() -> None:
    resp = await metricas.responder(_req(), _Pool(), [_senal(_ok)])
    assert resp.headers["content-type"] == "text/plain; version=0.0.4; charset=utf-8"


# ── dead-letter: SQL y señal ─────────────────────────────────────────────────────


async def test_contar_pendientes_sin_schemas_no_consulta() -> None:
    conn = _Conn()
    assert await deadletter.contar_pendientes(conn, [], ["g"]) == (0, None)  # type: ignore[arg-type]
    assert conn.sqls == []


async def test_contar_pendientes_califica_cada_schema() -> None:
    conn = _Conn(fetchrow={"n": 4, "m": datetime(2026, 1, 1, tzinfo=UTC)})
    n, m = await deadletter.contar_pendientes(conn, ["tenant_a", "tenant_b_c"], ["workflow-service"])  # type: ignore[arg-type]
    assert (n, m) == (4, datetime(2026, 1, 1, tzinfo=UTC))
    [sql] = conn.sqls
    assert '"tenant_a".evento_dead_letter' in sql and '"tenant_b_c".evento_dead_letter' in sql
    assert sql.count("UNION ALL") == 1
    assert "status = 'pending'" in sql and "origin_group = ANY($1::text[])" in sql


async def test_contar_pendientes_no_interpola_un_schema_dudoso() -> None:
    with pytest.raises(ValueError):
        await deadletter.contar_pendientes(_Conn(), ['tenant_x"; DROP TABLE y; --'], ["g"])  # type: ignore[arg-type]


async def test_senal_dlq_emite_pendientes_y_el_mas_antiguo_solo_si_hay() -> None:
    antiguo = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    con = _Conn(fetch=[{"slug": "demo"}, {"slug": "otra-entidad"}], fetchrow={"n": 3, "m": antiguo})
    res = await metricas.senal_dlq("workflow-service").obtener(_Pool(con))  # type: ignore[arg-type]
    assert res.ok
    vals = {m.familia: m.valor for m in res.muestras}
    assert vals["orpycamcp_dlq_pending_events"] == 3
    assert vals["orpycamcp_dlq_oldest_pending_timestamp_seconds"] == antiguo.timestamp()
    assert any('"tenant_otra_entidad".evento_dead_letter' in s for s in con.sqls)

    vacia = _Conn(fetch=[{"slug": "demo"}], fetchrow={"n": 0, "m": None})
    res0 = await metricas.senal_dlq("workflow-service").obtener(_Pool(vacia))  # type: ignore[arg-type]
    fams = {m.familia for m in res0.muestras}
    assert fams == {"orpycamcp_dlq_pending_events"}  # sin pendientes: la antigüedad AUSENTE, no 0


async def test_senal_dlq_con_la_tabla_ausente_es_no_verificada() -> None:
    class _Rota(_Conn):
        async def fetchrow(self, sql: str, *a: Any) -> Any:
            raise RuntimeError('relation "tenant_demo.evento_dead_letter" does not exist')

    res = await metricas.senal_dlq("g").obtener(_Pool(_Rota(fetch=[{"slug": "demo"}])))  # type: ignore[arg-type]
    assert res.ok is False and res.muestras == ()


async def test_solo_los_tenants_con_aprovisionamiento_activo_se_miden() -> None:
    con = _Conn(fetch=[{"slug": "demo"}])
    assert await metricas.esquemas_a_medir(con) == ["tenant_demo"]  # type: ignore[arg-type]
    assert "provisioning_status = 'active'" in con.sqls[0]
