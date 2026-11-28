# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Consumidores del bus de eventos y DLQ compartida (tanda T9, ADR-049).

AREA-10-06  dos consumidores del mismo grupo no toman el mismo evento: la toma es
            una fila por (grupo, evento) con ON CONFLICT, y la reclamación usa
            SKIP LOCKED (antes: nombre de consumidor por réplica para XCLAIM).
AREA-10-07  todo `ON CONFLICT` sobre `evento_dead_letter` incluye `origin_group`.
AREA-10-09  el dead-letter huérfano lleva la causa, no un texto vacío.
AREA-13-12  el último barrido y los dead-letters pendientes son visibles.

El comportamiento contra la tabla de verdad (la quinta entrega aparca con su
error, el huérfano dice que lo desconoce, sin rastro durable no se confirma) lo
prueba `services/docuboto/tests/document/integration/test_bus_eventos_integration.py`.
Aquí queda lo que se comprueba sin base: el contrato del código y el cableado.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from orpycamcp_common import events
from orpycamcp_common import health as health_common
from tests._chain import create_table
from tests._compose import glob_src, ruta_src

RAIZ = Path(__file__).resolve().parents[2]
SERVICIOS = RAIZ / "services"

pytestmark = pytest.mark.skipif(
    not SERVICIOS.is_dir(), reason="los directorios de servicios no están montados"
)


def _fuentes(patron: str):
    return glob_src(SERVICIOS, patron)


# ---------------------------------------------------------------------------
# AREA-10-06
# ---------------------------------------------------------------------------


def test_tomar_un_evento_es_una_fila_por_grupo_con_on_conflict() -> None:
    fuente = inspect.getsource(events.leer)
    assert "INSERT INTO evento_entregas" in fuente
    assert "ON CONFLICT DO NOTHING" in fuente


def test_la_reclamacion_no_toma_lo_que_otro_barrido_ya_tiene() -> None:
    fuente = inspect.getsource(events._reclaim_sweep)
    assert "FOR UPDATE SKIP LOCKED" in fuente


def test_ningun_worker_conserva_un_nombre_de_consumidor() -> None:
    """Sin XCLAIM no hay dueño por réplica: un `CONSUMER` que reaparezca es código muerto."""
    workers = _fuentes("*/app/workers/*.py")
    assert workers, "el censo no encontró workers: la ruta cambió"
    con_nombre = [str(w.relative_to(RAIZ)) for w in workers if re.search(r"^CONSUMER\s*=", w.read_text(), re.M)]
    assert not con_nombre, con_nombre


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


def test_la_pk_de_la_dlq_es_por_grupo() -> None:
    assert "PRIMARY KEY (origin_group, event_id)" in create_table("evento_dead_letter")


# ---------------------------------------------------------------------------
# AREA-10-09 / AREA-13-12
# ---------------------------------------------------------------------------


async def test_record_failure_es_best_effort() -> None:
    conn = AsyncMock()
    conn.execute.side_effect = ConnectionError("base caída")
    await events.record_failure(conn, "g", "e-1", RuntimeError("x"))  # no lanza


async def test_el_huerfano_sin_error_recordado_dice_que_lo_desconoce() -> None:
    conn = AsyncMock()
    conn.fetchval.return_value = None
    assert await events._recall_failure(conn, "g", "e-1") == events.LASTERR_DESCONOCIDO
    conn.fetchval.side_effect = ConnectionError("x")
    assert await events._recall_failure(conn, "g", "e-1") == events.LASTERR_DESCONOCIDO


def test_el_barrido_queda_en_consumer_status() -> None:
    events._estado_consumidores.pop("t9", None)
    events._record_sweep("t9", events.ReclaimStats(dead_lettered=1))
    estado = events.consumer_status()["t9"]
    assert estado["dead_lettered"] == 1
    assert estado["dead_lettered_total"] == 1
    assert estado["ultimo_barrido"]
    # Un segundo barrido sin nada que aparcar no borra el acumulado.
    events._record_sweep("t9", events.ReclaimStats())
    estado = events.consumer_status()["t9"]
    assert estado["dead_lettered"] == 0 and estado["dead_lettered_total"] == 1
    events._estado_consumidores.pop("t9", None)


def test_la_sonda_marca_degradada_una_dlq_con_pendientes() -> None:
    informe = health_common.consumer_report({"dead_lettered": 0}, 3)
    assert informe["dead_letter"] == "degraded"
    assert informe["detalle"]["dead_letter_pendientes"] == 3


def test_la_sonda_marca_ok_con_dlq_vacia_y_degradada_si_el_barrido_aparco() -> None:
    assert health_common.consumer_report({}, 0)["dead_letter"] == "ok"
    assert health_common.consumer_report({"dead_lettered": 2}, 0)["dead_letter"] == "degraded"


def test_un_conteo_que_falla_no_degrada_la_sonda() -> None:
    informe = health_common.consumer_report({}, None)
    assert informe["dead_letter"] == "ok"
    assert informe["detalle"]["dead_letter_pendientes"] is None


# ---------------------------------------------------------------------------
# Cableado entre piezas (revisión de testing-engineer, T9): probar events.* y
# health.* aislados no prueba que cada worker/servicio los use ni que se
# pongan de acuerdo en el nombre bajo el que se publica el consumidor.
# ---------------------------------------------------------------------------

_NOMBRES = {
    "workflow-service": ("workflow", ("workflow", "document_consumer.py")),
    "signature-service": ("signature", ("signature", "document_consumer.py")),
    "notification-service": ("notification", ("notification", "stream_worker.py")),
    "intelligence-gateway": ("intelligence-gateway", ("knowledge", "intelligence_consumer.py")),
}


def test_el_bucle_del_bus_registra_el_error_y_publica_el_barrido() -> None:
    bus = ruta_src("docuboto", "core", "bus.py").read_text()
    assert "events.record_failure(conn, group, event_id, exc)" in bus, (
        "el except del bucle no llama record_failure; el huérfano se aparcaría sin causa"
    )
    assert re.search(r"events\.reclaim_stale\([^)]*status_name=status_name", bus, re.S)


@pytest.mark.parametrize("servicio", sorted(_NOMBRES))
def test_el_nombre_supervisado_coincide_en_worker_lifespan_y_health(servicio: str) -> None:
    """Si difieren, /health/ready nunca encuentra el estado y la DLQ deja de verse sin error.

    Un proceso (ADR-047): el lifespan de la app los arranca y su `/health` los informa.
    """
    nombre, (dominio, archivo) = _NOMBRES[servicio]
    worker = ruta_src(dominio, "workers", archivo).read_text()
    assert "bus.consumir(" in worker and "on_dead_letter=_persist_dead_letter" in worker
    assert f'status_name="{nombre}"' in worker
    lifespan = ruta_src("docuboto", "core", "lifespan.py").read_text()
    assert f'tareas.start("{nombre}", ' in lifespan and "supervised=True" in lifespan
    health = ruta_src("docuboto", "routers", "health.py").read_text()
    assert f'"{nombre}": GRUPO_' in health
    assert "health_common.consumer_report(" in health


def test_los_cuatro_repositorios_no_vuelven_a_conflictuar_solo_por_event_id() -> None:
    """Anti-vacuidad del censo ON CONFLICT: ninguna sentencia de la DLQ (ni siquiera fuera del
    INSERT principal) puede apoyarse solo en event_id."""
    for repo in _fuentes("*/app/repositories/dead_letter_repository.py"):
        assert not re.search(r"ON CONFLICT\s*\(\s*event_id\s*\)", repo.read_text()), repo
