# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""ADR-028 (remediación) contra Postgres REAL, en document-service.

1. Aprobar la anulación cancela el paso activo en la MISMA transacción: si la
   transacción exterior revierte, ni el radicado queda anulado ni el paso cancelado.
2. `radicados.status` es de un conjunto cerrado (CHECK, migración 034), y la
   migración no tumba el arranque si hay datos heredados fuera del conjunto.

`flow_steps`/`flow_events` son del workflow-service y su DDL no viaja en esta imagen:
aquí van sus MÍNIMOS (las columnas que la anulación escribe).
"""

from __future__ import annotations

import json
import uuid

import asyncpg
import pytest

from app.core.database import tenant_schema
from app.repositories.anulacion_repository import AnulacionRepository
from migrations import runner

pytestmark = pytest.mark.asyncio

_FLUJO_DDL = """
CREATE TABLE flow_steps (
    id UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    radicado_id UUID NOT NULL, tracking_number VARCHAR(50) NOT NULL, step_number INTEGER NOT NULL,
    to_dept VARCHAR(100) NOT NULL, to_dept_code INTEGER,
    status VARCHAR(20) NOT NULL DEFAULT 'pending',
    completed_at TIMESTAMPTZ
);
CREATE TABLE flow_events (
    id UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
    radicado_id UUID NOT NULL, tracking_number VARCHAR(50) NOT NULL, tipo_tx VARCHAR(40) NOT NULL,
    from_dept TEXT, to_dept TEXT, from_dept_code INTEGER, to_dept_code INTEGER, actor UUID, comentario TEXT,
    detalles JSONB NOT NULL DEFAULT '{}'::jsonb, ts TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


async def _preparar(conn: asyncpg.Connection, slug: str, *, con_flujo: bool = True) -> AnulacionRepository:
    await conn.execute(f'SET search_path = "{tenant_schema(slug)}", public')
    if con_flujo:
        await conn.execute(_FLUJO_DDL)
    return AnulacionRepository(conn)


async def _radicado(conn: asyncpg.Connection, status: str = "registered") -> tuple[uuid.UUID, str]:
    tn = f"2026-DEMO-S-{uuid.uuid4().hex[:6]}"
    rid = await conn.fetchval(
        "INSERT INTO radicados (tracking_number, doc_type, year, sequence, subject, tenant_code, status) "
        "VALUES ($1, 'S', 2026, 1, 'ASUNTO', 'DEMO', $2) RETURNING id",
        tn,
        status,
    )
    return rid, tn


async def _paso(conn: asyncpg.Connection, rid: uuid.UUID, tn: str, n: int, status: str) -> uuid.UUID:
    return await conn.fetchval(
        "INSERT INTO flow_steps (radicado_id, tracking_number, step_number, to_dept, to_dept_code, "
        "status) VALUES ($1, $2, $3, 'Jurídica', 410, $4) RETURNING id",
        rid,
        tn,
        n,
        status,
    )


async def _anular(repo: AnulacionRepository, conn: asyncpg.Connection, rid: uuid.UUID, decide: uuid.UUID):
    sol = await repo.insert_solicitud(radicado_id=rid, causal="ANU-ERR", motivo="x", solicitado_por=uuid.uuid4())
    return await repo.decide(
        anulacion_id=sol["id"], radicado_id=rid, aprobar=True, decidido_por=decide, observacion="ok"
    )


async def test_aprobar_la_anulacion_cancela_el_paso_activo_y_deja_su_evento(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    repo = await _preparar(conn, slug)
    rid, tn = await _radicado(conn)
    viejo = await _paso(conn, rid, tn, 1, "completed")
    activo = await _paso(conn, rid, tn, 2, "in_progress")
    decide = uuid.uuid4()

    await _anular(repo, conn, rid, decide)

    assert await conn.fetchval("SELECT status FROM radicados WHERE id = $1", rid) == "anulado"
    assert await conn.fetchval("SELECT status FROM flow_steps WHERE id = $1", activo) == "cancelled"
    assert await conn.fetchval("SELECT completed_at IS NOT NULL FROM flow_steps WHERE id = $1", activo)
    assert await conn.fetchval("SELECT status FROM flow_steps WHERE id = $1", viejo) == "completed"
    ev = await conn.fetchrow("SELECT tipo_tx, actor, detalles, to_dept_code FROM flow_events WHERE radicado_id = $1", rid)
    assert ev["to_dept_code"] == 410  # la tenencia sigue por CÓDIGO tras anular (ADR-032)
    assert (ev["tipo_tx"], ev["actor"]) == ("anulacion", decide)
    assert json.loads(ev["detalles"])["step_id"] == str(activo)


async def test_si_la_transaccion_exterior_revierte_ni_el_radicado_ni_el_paso_cambian(tenant_ctx) -> None:
    """Atomicidad: la anulación y la cancelación del paso son UN acto (el servicio
    envuelve `decide` y `audit.append` en la misma transacción)."""
    slug, conn = tenant_ctx
    repo = await _preparar(conn, slug)
    rid, tn = await _radicado(conn)
    activo = await _paso(conn, rid, tn, 1, "pending")

    with pytest.raises(RuntimeError, match="audit"):
        async with conn.transaction():
            await _anular(repo, conn, rid, uuid.uuid4())
            raise RuntimeError("audit caído")  # p. ej. `audit.append` fallando después

    assert await conn.fetchval("SELECT status FROM radicados WHERE id = $1", rid) == "registered"
    assert await conn.fetchval("SELECT status FROM flow_steps WHERE id = $1", activo) == "pending"
    assert await conn.fetchval("SELECT count(*) FROM flow_events WHERE radicado_id = $1", rid) == 0


async def test_sin_tablas_de_flujo_la_anulacion_sigue_funcionando(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    repo = await _preparar(conn, slug, con_flujo=False)
    rid, _ = await _radicado(conn)
    await _anular(repo, conn, rid, uuid.uuid4())
    assert await conn.fetchval("SELECT status FROM radicados WHERE id = $1", rid) == "anulado"


async def test_rechazar_no_cancela_el_paso(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    repo = await _preparar(conn, slug)
    rid, tn = await _radicado(conn)
    activo = await _paso(conn, rid, tn, 1, "pending")
    sol = await repo.insert_solicitud(radicado_id=rid, causal="ANU-ERR", motivo="x", solicitado_por=uuid.uuid4())
    await repo.decide(
        anulacion_id=sol["id"], radicado_id=rid, aprobar=False, decidido_por=uuid.uuid4(), observacion="no"
    )
    assert await conn.fetchval("SELECT status FROM flow_steps WHERE id = $1", activo) == "pending"
    assert await conn.fetchval("SELECT status FROM radicados WHERE id = $1", rid) == "registered"


# --- 034: CHECK del estado --------------------------------------------------------------


async def _correr_034(conn: asyncpg.Connection, slug: str) -> None:
    sql = next(p for p in runner._sql_files(runner.TENANT_DIR) if "radicado_status_check" in p.name)
    async with conn.transaction():
        await conn.execute(f'SET LOCAL search_path = "{tenant_schema(slug)}", public')
        await conn.execute(sql.read_text())


async def test_el_check_rechaza_un_estado_fuera_del_conjunto_y_acepta_los_cinco(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path = "{tenant_schema(slug)}", public')
    for estado in ("registered", "distributed", "in_progress", "closed", "anulado"):
        await _radicado(conn, estado)
    with pytest.raises(asyncpg.CheckViolationError, match="radicados_status_check"):
        await _radicado(conn, "archivado")
    # La restricción nace validada en un tenant sin datos heredados.
    assert await conn.fetchval(
        "SELECT convalidated FROM pg_constraint WHERE conname = 'radicados_status_check' "
        "AND conrelid = 'radicados'::regclass"
    )


async def test_034_con_datos_heredados_no_falla_deja_el_aviso_y_sigue_protegiendo_lo_nuevo(tenant_ctx) -> None:
    slug, conn = tenant_ctx
    await conn.execute(f'SET search_path = "{tenant_schema(slug)}", public')
    await conn.execute("ALTER TABLE radicados DROP CONSTRAINT radicados_status_check")
    heredado, _ = await _radicado(conn, "recibido")  # un valor que ninguna máquina de estados conoce

    await _correr_034(conn, slug)  # NO lanza

    fila = await conn.fetchrow(
        "SELECT convalidated FROM pg_constraint WHERE conname = 'radicados_status_check' "
        "AND conrelid = 'radicados'::regclass"
    )
    assert fila is not None and fila["convalidated"] is False  # sin validar: rige solo lo nuevo
    with pytest.raises(asyncpg.CheckViolationError):
        await _radicado(conn, "otro_invento")
    # Corregido el dato, la migración (idempotente) valida la restricción.
    await conn.execute("UPDATE radicados SET status = 'closed' WHERE id = $1", heredado)
    await _correr_034(conn, slug)
    assert await conn.fetchval(
        "SELECT convalidated FROM pg_constraint WHERE conname = 'radicados_status_check' "
        "AND conrelid = 'radicados'::regclass"
    )


async def test_dos_decisiones_sobre_la_misma_solicitud_la_segunda_es_none_y_nada_se_toca(tenant_ctx) -> None:
    """[G] Contra Postgres real: la solicitud ya `aprobada` no se vuelve a decidir.
    El repositorio devuelve `None` (no revienta con un `assert`, que era un 500 en
    la carrera) y no reescribe quién decidió ni cuándo, ni toca radicado ni pasos."""
    slug, conn = tenant_ctx
    repo = await _preparar(conn, slug)
    rid, tn = await _radicado(conn)
    await _paso(conn, rid, tn, 1, "pending")
    sol = await repo.insert_solicitud(radicado_id=rid, causal="ANU-ERR", motivo="x", solicitado_por=uuid.uuid4())
    primero = uuid.uuid4()
    ok = await repo.decide(
        anulacion_id=sol["id"], radicado_id=rid, aprobar=True, decidido_por=primero, observacion="ok"
    )
    assert ok is not None and ok["estado"] == "aprobada"
    await conn.execute("UPDATE radicados SET status = 'registered' WHERE id = $1", rid)  # centinela
    await conn.execute("UPDATE flow_steps SET status = 'pending' WHERE radicado_id = $1", rid)

    segundo = await repo.decide(
        anulacion_id=sol["id"], radicado_id=rid, aprobar=True, decidido_por=uuid.uuid4(), observacion="otra"
    )

    assert segundo is None
    assert await conn.fetchval("SELECT decidido_por FROM radicado_anulacion WHERE id = $1", sol["id"]) == primero
    assert await conn.fetchval("SELECT status FROM radicados WHERE id = $1", rid) == "registered"
    assert await conn.fetchval("SELECT status FROM flow_steps WHERE radicado_id = $1", rid) == "pending"
