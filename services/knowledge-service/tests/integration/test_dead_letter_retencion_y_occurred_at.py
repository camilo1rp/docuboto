# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""AREA-10-08 (knowledge): purga de la DLQ terminal y segunda red por `occurred_at` (PG REAL).

* La DLQ de knowledge no tenía retención: `resolved`/`discarded` se acumulaban sin fin.
  La purga es la misma de workflow, acotada por tenant y por GRUPO (la tabla es de cuatro
  consumidores).
* Un `reclassified` VIEJO reentregado desde la DLQ no debe deshacer una reclasificación
  posterior. `nivel_evento_at` guarda el instante del último evento aplicado.
"""

from __future__ import annotations

import asyncpg
import pytest

from app.repositories.dead_letter_repository import DeadLetterRepository
from app.services import ingestion

TRACKING = "2026-ITEST-E-000077"
T1 = "2026-10-09T10:00:00Z"
T0 = "2026-10-09T09:00:00Z"  # anterior a T1
T2 = "2026-10-09T11:00:00Z"
GRUPO = "knowledge-ingest"


async def _nivel(conn: asyncpg.Connection) -> int:
    return await conn.fetchval(
        "SELECT nivel_seguridad FROM knowledge_chunk "
        "WHERE source_type = 'radicado' AND source_ref = $1",
        TRACKING,
    )


async def _reclasificar(conn: asyncpg.Connection, slug: str, nivel: int, at: str | None) -> None:
    await ingestion.apply_reclassification(
        conn,
        tenant_slug=slug,
        payload={"tracking_number": TRACKING, "nivel_nuevo": nivel},
        occurred_at=at,
    )


@pytest.mark.integration
async def test_un_evento_viejo_no_baja_el_nivel_pero_se_aplica_sin_error(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    await _reclasificar(conn, slug, 3, T1)
    await _reclasificar(conn, slug, 1, T0)  # replay tardío de un evento ANTERIOR
    assert await _nivel(conn) == 3
    stamp = await conn.fetchval(
        "SELECT nivel_evento_at FROM knowledge_chunk WHERE source_ref = $1", TRACKING
    )
    assert stamp.isoformat().startswith("2026-10-09T10:00:00"), "el instante no retrocede"


@pytest.mark.integration
async def test_un_evento_mas_nuevo_si_puede_bajar_el_nivel(tenant_ctx) -> None:
    """Control positivo: la red no congela el nivel, solo ordena por tiempo."""
    slug, conn = tenant_ctx
    await _reclasificar(conn, slug, 3, T1)
    await _reclasificar(conn, slug, 1, T2)
    assert await _nivel(conn) == 1


@pytest.mark.integration
async def test_sin_occurred_at_solo_puede_subir(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    await _reclasificar(conn, slug, 3, T1)
    await _reclasificar(conn, slug, 1, None)
    assert await _nivel(conn) == 3
    await _reclasificar(conn, slug, 3, "no-es-una-fecha")  # no interpretable = ausente
    await _reclasificar(conn, slug, 1, "no-es-una-fecha")
    assert await _nivel(conn) == 3


async def _fila(conn: asyncpg.Connection, eid: str, grupo: str, status: str, dias: int) -> None:
    await conn.execute(
        "INSERT INTO evento_dead_letter (event_id, tenant_slug, payload, failure, origin_group, "
        "origin_stream, status, dead_lettered_at, resolved_at) "
        "VALUES ($1, 'itest', '{}'::jsonb, '{}'::jsonb, $2, 's', $3, now(), "
        "CASE WHEN $3 = 'pending' THEN NULL ELSE now() - make_interval(days => $4) END)",
        eid,
        grupo,
        status,
        dias,
    )


@pytest.mark.integration
async def test_la_purga_borra_solo_terminales_viejas_del_propio_grupo(tenant_ctx) -> None:
    _slug, conn = tenant_ctx
    await _fila(conn, "viejo-resuelto", GRUPO, "resolved", 120)
    await _fila(conn, "viejo-descartado", GRUPO, "discarded", 120)
    await _fila(conn, "reciente-resuelto", GRUPO, "resolved", 10)
    await _fila(conn, "viejo-pendiente", GRUPO, "pending", 0)
    await _fila(conn, "viejo-de-otro-grupo", "workflow-service", "resolved", 120)

    borradas = await DeadLetterRepository(conn).purge_terminal(90, "itest", GRUPO)

    assert borradas == 2
    quedan = {r["event_id"] for r in await conn.fetch("SELECT event_id FROM evento_dead_letter")}
    assert quedan == {"reciente-resuelto", "viejo-pendiente", "viejo-de-otro-grupo"}


@pytest.mark.integration
async def test_la_purga_rechaza_una_ventana_que_borraria_todo(tenant_ctx) -> None:
    _slug, conn = tenant_ctx
    with pytest.raises(ValueError):
        await DeadLetterRepository(conn).purge_terminal(0, "itest", GRUPO)
