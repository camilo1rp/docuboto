# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Consumidores de Redis Streams y DLQ compartida (tanda T9).

AREA-10-06  nombre de consumidor por réplica, no un literal.
AREA-10-07  todo `ON CONFLICT` sobre `evento_dead_letter` incluye `origin_group`.
AREA-10-09  el dead-letter huérfano lleva la causa, no un texto vacío.
AREA-13-12  el último barrido y el tamaño de la DLQ son visibles.
"""

from __future__ import annotations

import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from orpycamcp_common import events
from orpycamcp_common import health as health_common

RAIZ = Path(__file__).resolve().parents[2]
SERVICIOS = RAIZ / "services"

pytestmark = pytest.mark.skipif(
    not SERVICIOS.is_dir(), reason="los directorios de servicios no están montados"
)


def _fuentes(patron: str):
    return sorted(SERVICIOS.glob(patron))


# ---------------------------------------------------------------------------
# AREA-10-06
# ---------------------------------------------------------------------------


def test_consumer_name_distingue_replicas(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HOSTNAME", "a")
    a = events.consumer_name("x")
    monkeypatch.setenv("HOSTNAME", "b")
    b = events.consumer_name("x")
    assert a != b
    assert a.startswith("x-") and b.startswith("x-")


def test_consumer_name_sin_hostname_cae_al_del_equipo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HOSTNAME", raising=False)
    assert events.consumer_name("svc").startswith("svc-") and len(events.consumer_name("svc")) > 4


_LITERAL = re.compile(r"""^CONSUMER\s*=\s*["'][^"']*["']""", re.M)


def test_ningun_worker_fija_el_nombre_de_consumidor_con_un_literal() -> None:
    workers = _fuentes("*/app/workers/*.py")
    assert workers, "el censo no encontró workers: la ruta cambió"
    literales = [str(w.relative_to(RAIZ)) for w in workers if _LITERAL.search(w.read_text())]
    assert not literales, f"CONSUMER fijo (rompe XCLAIM entre réplicas): {literales}"
    sin_derivar = [
        str(w.relative_to(RAIZ))
        for w in workers
        if re.search(r"^CONSUMER\s*=", w.read_text(), re.M)
        and "events.consumer_name(" not in w.read_text()
    ]
    assert not sin_derivar, f"CONSUMER sin events.consumer_name: {sin_derivar}"


# ---------------------------------------------------------------------------
# AREA-10-07
# ---------------------------------------------------------------------------


def _on_conflict_de_la_dlq(texto: str) -> list[str]:
    """Cláusulas ON CONFLICT de sentencias que insertan en evento_dead_letter."""
    hallazgos = []
    for m in re.finditer(r"INSERT INTO evento_dead_letter(.*?)(?:RETURNING|\"\"\"|;)", texto, re.S):
        hallazgos.extend(re.findall(r"ON CONFLICT\s*\([^)]*\)", m.group(1)))
    return hallazgos


def test_todo_on_conflict_de_la_dlq_incluye_el_grupo() -> None:
    repos = _fuentes("*/app/repositories/dead_letter_repository.py")
    assert len(repos) >= 3, "deben ser los 3 consumidores: workflow, signature, notification"
    for repo in repos:
        cl = _on_conflict_de_la_dlq(repo.read_text())
        assert cl, f"{repo}: no se halló el INSERT con ON CONFLICT"
        for c in cl:
            assert "origin_group" in c and "event_id" in c, (
                f"{repo.relative_to(RAIZ)}: {c} — con PK solo por event_id el aparcamiento "
                "del segundo grupo se descarta en silencio"
            )


def test_las_migraciones_de_pk_por_grupo_son_la_misma() -> None:
    sqls = _fuentes("*/migrations/tenant/*_dead_letter_pk_por_grupo.sql")
    assert len(sqls) == 4
    cuerpos = {s.read_text() for s in sqls}
    assert len(cuerpos) == 1, "las 4 copias deben ser idénticas (corre la primera que llegue)"
    assert "PRIMARY KEY (origin_group, event_id)" in cuerpos.pop()


# ---------------------------------------------------------------------------
# AREA-10-09 / AREA-13-12 — doble de Redis con estado
# ---------------------------------------------------------------------------


class _FakeRedis:
    def __init__(self, pending: list[dict], fields) -> None:
        self.pending = pending
        self.fields = fields
        self.hashes: dict[str, dict[str, str]] = {}
        self.xadded: list[dict] = []
        self.acked: list[str] = []
        self.expired: list[str] = []

    async def xpending_range(self, *a, **k):
        return self.pending

    async def xclaim(self, stream, group, consumer, min_idle, ids):
        return [(ids[0], self.fields)]

    async def xadd(self, stream, fields):
        self.xadded.append(fields)
        return "1-0"

    async def xack(self, stream, group, msg_id):
        self.acked.append(msg_id)
        return 1

    async def hset(self, key, field, value):
        self.hashes.setdefault(key, {})[field] = value

    async def expire(self, key, ttl):
        self.expired.append(key)

    async def hget(self, key, field):
        return self.hashes.get(key, {}).get(field)

    async def hdel(self, key, field):
        self.hashes.get(key, {}).pop(field, None)


def _entrada(msg_id: str, veces: int) -> dict:
    return {"message_id": msg_id, "consumer": "otro", "time_since_delivered": 70_000,
            "times_delivered": veces}


def _campos() -> dict:
    return events.to_fields(events.build_event("x.y", tenant_slug="demo", payload={"k": 1}))


async def _barrer(redis, handler, **kw):
    return await events.reclaim_stale(
        redis, "s", "g", "c", handler=handler, min_idle_ms=60_000, max_deliveries=5,
        deadletter_stream="dl", **kw,
    )


async def test_el_huerfano_lleva_el_ultimo_error_del_fallo_previo() -> None:
    campos = _campos()
    handler = AsyncMock(side_effect=RuntimeError("embedding caído"))
    # Barrido 1: el reproceso falla por debajo del umbral y deja constancia del error.
    r = _FakeRedis([_entrada("1-1", 2)], campos)
    stats = await _barrer(r, handler)
    assert stats.reprocessed_fail == 1 and not r.xadded
    assert "embedding caído" in r.hashes["orpycamcp:g:lasterr"]["1-1"]
    # Barrido 2: el mensaje ya está en el umbral y no hay intento fresco (huérfano).
    r.pending = [_entrada("1-1", 5)]
    stats = await _barrer(r, handler, on_dead_letter=AsyncMock())
    assert stats.dead_lettered == 1
    assert "embedding caído" in r.xadded[0]["dead_letter_last_error"]
    assert "1-1" not in r.hashes["orpycamcp:g:lasterr"], "el error no se olvida tras aparcar"


async def test_el_huerfano_sin_error_previo_dice_que_lo_desconoce_y_no_vacio() -> None:
    r = _FakeRedis([_entrada("2-1", 5)], _campos())
    await _barrer(r, AsyncMock(), on_dead_letter=AsyncMock())
    ultimo = r.xadded[0]["dead_letter_last_error"]
    assert ultimo and ultimo == events.LASTERR_DESCONOCIDO


async def test_record_failure_es_best_effort() -> None:
    redis = AsyncMock()
    redis.hset.side_effect = ConnectionError("redis caído")
    await events.record_failure(redis, "g", "1-1", RuntimeError("x"))  # no lanza


async def test_el_barrido_queda_en_consumer_status() -> None:
    events._estado_consumidores.pop("t9", None)
    r = _FakeRedis([_entrada("3-1", 5)], _campos())
    await _barrer(r, AsyncMock(), on_dead_letter=AsyncMock(), status_name="t9")
    estado = events.consumer_status()["t9"]
    assert estado["dead_lettered"] == 1
    assert estado["dead_lettered_total"] == 1
    assert estado["ultimo_barrido"]
    # Un segundo barrido sin nada que aparcar no borra el acumulado.
    r.pending = []
    await _barrer(r, AsyncMock(), status_name="t9")
    estado = events.consumer_status()["t9"]
    assert estado["dead_lettered"] == 0 and estado["dead_lettered_total"] == 1
    events._estado_consumidores.pop("t9", None)


async def test_todo_worker_publica_sus_estadisticas_de_barrido() -> None:
    workers = _fuentes("*/app/workers/*.py")
    for w in workers:
        t = w.read_text()
        if "events.reclaim_stale(" in t:
            assert "status_name=" in t, f"{w.relative_to(RAIZ)}: el ReclaimStats se descarta"


async def test_la_sonda_marca_degradada_una_dlq_con_entradas() -> None:
    redis = AsyncMock()
    redis.xlen.return_value = 3
    informe = await health_common.consumer_report(redis, {"dead_lettered": 0}, "dl")
    assert informe["dead_letter"] == "degraded"
    assert informe["detalle"]["dead_letter_stream_len"] == 3


async def test_la_sonda_marca_ok_con_dlq_vacia_y_degradada_si_el_barrido_aparco() -> None:
    redis = AsyncMock()
    redis.xlen.return_value = 0
    assert (await health_common.consumer_report(redis, {}, "dl"))["dead_letter"] == "ok"
    assert (
        await health_common.consumer_report(redis, {"dead_lettered": 2}, "dl")
    )["dead_letter"] == "degraded"


async def test_un_xlen_que_falla_no_tumba_la_sonda_ni_la_degrada() -> None:
    redis = AsyncMock()
    redis.xlen.side_effect = ConnectionError("x")
    informe = await health_common.consumer_report(redis, {}, "dl")
    assert informe["dead_letter"] == "ok"
    assert informe["detalle"]["dead_letter_stream_len"] is None


# ---------------------------------------------------------------------------
# Cableado entre piezas (revisión de testing-engineer, T9): probar events.* y
# health.* aislados no prueba que cada worker/servicio los use ni que se
# pongan de acuerdo en el nombre bajo el que se publica el consumidor.
# ---------------------------------------------------------------------------

_NOMBRES = {
    "workflow-service": "workflow",
    "signature-service": "signature",
    "notification-service": "notification",
}


def _worker(servicio: str) -> Path:
    ws = [w for w in _fuentes(f"{servicio}/app/workers/*.py") if "events.reclaim_stale(" in w.read_text()]
    assert len(ws) == 1, f"{servicio}: se esperaba exactamente un worker con reclaim_stale, hay {ws}"
    return ws[0]


@pytest.mark.parametrize("servicio", sorted(_NOMBRES))
def test_el_worker_registra_el_error_y_usa_su_nombre_por_replica(servicio: str) -> None:
    t = _worker(servicio).read_text()
    assert "events.record_failure(redis, GROUP, msg_id, exc)" in t, (
        f"{servicio}: el except del bucle no llama record_failure; el huérfano se aparca sin causa"
    )
    # el nombre por réplica debe llegar al XREADGROUP *y* al XCLAIM de reclaim_stale
    assert re.search(r"xreadgroup\(\s*GROUP,\s*CONSUMER\b", t)
    assert re.search(r"events\.reclaim_stale\([^)]*?\bCONSUMER,", t, re.S)


@pytest.mark.parametrize("servicio,nombre", sorted(_NOMBRES.items()))
def test_el_nombre_supervisado_coincide_en_worker_main_y_health(servicio: str, nombre: str) -> None:
    """Si difieren, /health/ready nunca encuentra el estado y la DLQ deja de verse sin error."""
    assert f'status_name="{nombre}"' in _worker(servicio).read_text()
    main = (SERVICIOS / servicio / "app/main.py").read_text()
    assert f'name="{nombre}"' in main and "spawn_supervised" in main
    health = (SERVICIOS / servicio / "app/routers/health.py").read_text()
    assert f'_CONSUMIDOR = "{nombre}"' in health
    assert "health_common.consumer_report(" in health and "DEADLETTER_STREAM" in health


def test_los_cuatro_repositorios_no_vuelven_a_conflictuar_solo_por_event_id() -> None:
    """Anti-vacuidad del censo ON CONFLICT: ninguna sentencia de la DLQ (ni siquiera fuera del
    INSERT principal) puede apoyarse solo en event_id."""
    for repo in _fuentes("*/app/repositories/dead_letter_repository.py"):
        assert not re.search(r"ON CONFLICT\s*\(\s*event_id\s*\)", repo.read_text()), repo
