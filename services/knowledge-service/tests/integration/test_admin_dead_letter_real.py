# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""AREA-10-10: inspección y reproceso del dead-letter de knowledge, contra Postgres REAL.

Lo crítico es el replay de `document.radicado.purged`/`annulled`: si el evento cae al
DLQ, el chunk sigue buscable con su texto hasta que alguien lo reprocese. Aquí se
recorre el camino completo por el router (permiso, clearance, transacción, asiento).
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.database import get_tenant_conn
from app.main import app
from app.repositories.dead_letter_repository import DeadLetterRepository
from app.services import ingestion
from app.workers.document_consumer import GROUP

pytestmark = pytest.mark.integration

TRACKING = "2026-ITEST-E-000077"

_STUBS = """
CREATE TABLE auth_users (id UUID PRIMARY KEY, is_root BOOLEAN NOT NULL DEFAULT false);
CREATE TABLE auth_permissions (id UUID PRIMARY KEY DEFAULT gen_random_uuid(), nombre TEXT NOT NULL);
CREATE TABLE auth_memberships (user_id UUID NOT NULL, group_id UUID NOT NULL);
CREATE TABLE auth_group_permissions (group_id UUID NOT NULL, permission_id UUID NOT NULL, crud INT NOT NULL);
CREATE TABLE role_clearance (group_id UUID NOT NULL, max_level INT NOT NULL);
CREATE TABLE IF NOT EXISTS radicados (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tracking_number TEXT NOT NULL,
    nivel_seguridad SMALLINT NOT NULL DEFAULT 1
);
"""


async def _usuario(conn: asyncpg.Connection, *, crud: int, clearance: int) -> uuid.UUID:
    uid, gid = uuid.uuid4(), uuid.uuid4()
    pid = await conn.fetchval(
        "INSERT INTO auth_permissions (nombre) VALUES ('PERM_DLQ_ADMIN') RETURNING id"
    )
    await conn.execute("INSERT INTO auth_users (id) VALUES ($1)", uid)
    await conn.execute("INSERT INTO auth_memberships VALUES ($1, $2)", uid, gid)
    await conn.execute("INSERT INTO auth_group_permissions VALUES ($1, $2, $3)", gid, pid, crud)
    await conn.execute("INSERT INTO role_clearance VALUES ($1, $2)", gid, clearance)
    return uid


@pytest_asyncio.fixture
async def ctx(tenant_ctx) -> AsyncGenerator[tuple[str, asyncpg.Connection, AsyncClient], None]:
    slug, conn = tenant_ctx
    await conn.execute(_STUBS)

    async def _dep() -> AsyncGenerator[asyncpg.Connection, None]:
        yield conn

    app.dependency_overrides[get_tenant_conn] = _dep
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as ac:
        yield slug, conn, ac
    app.dependency_overrides.clear()


async def _aparcar(
    conn: asyncpg.Connection,
    slug: str,
    event_type: str,
    *,
    grupo: str = GROUP,
    extra: dict | None = None,
) -> str:
    eid = f"evt-{uuid.uuid4().hex[:10]}"
    envelope = {
        "event_id": eid,
        "event_type": event_type,
        "tenant_slug": slug,
        "payload": {"tracking_number": TRACKING, "id": "doc-77", **(extra or {})},
    }
    row = await DeadLetterRepository(conn).insert_if_new(
        event_id=eid,
        event_type=event_type,
        correlation_id=None,
        tenant_slug=slug,
        payload_json=json.dumps(envelope),
        failure_json=json.dumps({"dead_letter_last_error": "boom"}),
        origin_stream="orpycamcp.document.events",
        origin_group=grupo,
    )
    assert row is not None
    return eid


async def _chunk(conn: asyncpg.Connection) -> asyncpg.Record:
    return await conn.fetchrow(
        "SELECT voided, texto FROM knowledge_chunk "
        "WHERE source_type = 'radicado' AND source_ref = $1",
        TRACKING,
    )


async def _indexar(conn: asyncpg.Connection, slug: str, nivel: int) -> None:
    await conn.execute(
        "INSERT INTO radicados (tracking_number, nivel_seguridad) VALUES ($1, $2)", TRACKING, nivel
    )
    await ingestion.ingest_radicado_created(
        conn,
        tenant_slug=slug,
        payload={"tracking_number": TRACKING, "nivel_seguridad": nivel, "subject": "Asunto"},
    )
    assert (await _chunk(conn))["voided"] is False


def _h(slug: str, uid: uuid.UUID) -> dict[str, str]:
    return {"X-Tenant-Slug": slug, "X-User-Id": str(uid)}


async def test_replay_de_un_purged_aparcado_deja_el_chunk_voided(ctx) -> None:
    slug, conn, client = ctx
    await _indexar(conn, slug, 1)
    admin = await _usuario(conn, crud=3, clearance=3)
    eid = await _aparcar(conn, slug, "document.radicado.purged")

    r = await client.post(
        f"/api/v1/knowledge/admin/deadletter/{eid}/replay", headers=_h(slug, admin)
    )

    assert r.status_code == 200, r.text
    chunk = await _chunk(conn)
    assert chunk["voided"] is True and chunk["texto"] == ""
    assert await conn.fetchval(
        "SELECT status FROM evento_dead_letter WHERE event_id = $1", eid
    ) == "resolved"
    asiento = await conn.fetchval(
        "SELECT count(*) FROM public.audit_log WHERE tenant_slug = $1 "
        "AND action = 'evento.dead_letter.replayed' AND object_ref = $2",
        slug,
        eid,
    )
    assert asiento == 1


async def test_sin_el_permiso_da_403_y_no_toca_nada(ctx) -> None:
    slug, conn, client = ctx
    await _indexar(conn, slug, 1)
    sin_permiso = uuid.uuid4()
    await conn.execute("INSERT INTO auth_users (id) VALUES ($1)", sin_permiso)
    eid = await _aparcar(conn, slug, "document.radicado.purged")

    r = await client.post(
        f"/api/v1/knowledge/admin/deadletter/{eid}/replay", headers=_h(slug, sin_permiso)
    )

    assert r.status_code == 403
    assert (await _chunk(conn))["voided"] is False


async def test_el_nivel_de_lectura_no_basta_para_reprocesar(ctx) -> None:
    slug, conn, client = ctx
    await _indexar(conn, slug, 1)
    lector = await _usuario(conn, crud=1, clearance=3)
    eid = await _aparcar(conn, slug, "document.radicado.purged")

    ver = await client.get(f"/api/v1/knowledge/admin/deadletter/{eid}", headers=_h(slug, lector))
    replay = await client.post(
        f"/api/v1/knowledge/admin/deadletter/{eid}/replay", headers=_h(slug, lector)
    )

    assert ver.status_code == 200
    assert replay.status_code == 403
    assert (await _chunk(conn))["voided"] is False


async def test_un_admin_sin_clearance_recibe_404_neutro(ctx) -> None:
    slug, conn, client = ctx
    await _indexar(conn, slug, 3)  # radicado CLASIFICADO
    admin_bajo = await _usuario(conn, crud=3, clearance=1)
    eid = await _aparcar(conn, slug, "document.radicado.purged")

    r = await client.post(
        f"/api/v1/knowledge/admin/deadletter/{eid}/replay", headers=_h(slug, admin_bajo)
    )
    lista = await client.get("/api/v1/knowledge/admin/deadletter/", headers=_h(slug, admin_bajo))

    assert r.status_code == 404
    assert lista.json() == [] and lista.headers["X-Total-Count"] == "0"
    assert (await _chunk(conn))["voided"] is False


async def test_no_ve_ni_reprocesa_lo_aparcado_por_otro_consumidor(ctx) -> None:
    slug, conn, client = ctx
    await _indexar(conn, slug, 1)
    admin = await _usuario(conn, crud=3, clearance=3)
    ajeno = await _aparcar(conn, slug, "document.radicado.purged", grupo="workflow-service")

    lista = await client.get("/api/v1/knowledge/admin/deadletter/", headers=_h(slug, admin))
    r = await client.post(
        f"/api/v1/knowledge/admin/deadletter/{ajeno}/replay", headers=_h(slug, admin)
    )

    assert lista.json() == []
    assert r.status_code == 404
    assert (await _chunk(conn))["voided"] is False


async def test_un_tipo_que_knowledge_no_aplica_no_se_marca_resuelto(ctx) -> None:
    slug, conn, client = ctx
    admin = await _usuario(conn, crud=3, clearance=3)
    eid = await _aparcar(conn, slug, "workflow.step.completed")

    r = await client.post(
        f"/api/v1/knowledge/admin/deadletter/{eid}/replay", headers=_h(slug, admin)
    )

    assert r.status_code == 422
    assert await conn.fetchval(
        "SELECT status FROM evento_dead_letter WHERE event_id = $1", eid
    ) == "pending"


async def test_descartar_exige_motivo_y_deja_asiento(ctx) -> None:
    slug, conn, client = ctx
    await _indexar(conn, slug, 1)
    admin = await _usuario(conn, crud=3, clearance=3)
    eid = await _aparcar(conn, slug, "document.radicado.purged")

    r = await client.post(
        f"/api/v1/knowledge/admin/deadletter/{eid}/discard",
        headers=_h(slug, admin),
        json={"reason": "duplicado verificado"},
    )

    assert r.status_code == 200
    assert await conn.fetchval(
        "SELECT status FROM evento_dead_letter WHERE event_id = $1", eid
    ) == "discarded"
    assert (await _chunk(conn))["voided"] is False  # descartar NO aplica el evento


# ---------------------------------------------------------------------------
# Revisión de testing-engineer (T9): detalle y descarte ajenos, y el mismo
# event_id aparcado por dos grupos (la PK compartida ya lo permite).
# ---------------------------------------------------------------------------


async def test_el_detalle_y_el_descarte_de_lo_ajeno_dan_404_sin_asiento_ni_cambio(ctx) -> None:
    slug, conn, client = ctx
    admin = await _usuario(conn, crud=3, clearance=3)
    ajeno = await _aparcar(conn, slug, "document.radicado.purged", grupo="workflow-service")

    detalle = await client.get(
        f"/api/v1/knowledge/admin/deadletter/{ajeno}", headers=_h(slug, admin)
    )
    descarte = await client.post(
        f"/api/v1/knowledge/admin/deadletter/{ajeno}/discard",
        headers=_h(slug, admin),
        json={"reason": "no es mío"},
    )

    assert detalle.status_code == 404 and descarte.status_code == 404
    assert await conn.fetchval(
        "SELECT status FROM evento_dead_letter WHERE event_id = $1", ajeno
    ) == "pending"
    assert not await conn.fetchval(
        "SELECT count(*) FROM public.audit_log WHERE object_ref = $1", ajeno
    )


async def test_mismo_event_id_en_dos_grupos_replay_solo_resuelve_la_fila_propia(ctx) -> None:
    slug, conn, client = ctx
    await _indexar(conn, slug, 1)
    admin = await _usuario(conn, crud=3, clearance=3)
    eid = await _aparcar(conn, slug, "document.radicado.purged")
    # El otro consumidor aparca el MISMO evento: antes de AREA-10-07 esto se perdía.
    await conn.execute(
        "INSERT INTO evento_dead_letter (event_id, event_type, tenant_slug, payload, failure, "
        "origin_stream, origin_group, dead_lettered_at) "
        "SELECT event_id, event_type, tenant_slug, payload, failure, origin_stream, "
        "'workflow-service', now() FROM evento_dead_letter WHERE event_id = $1",
        eid,
    )

    r = await client.post(
        f"/api/v1/knowledge/admin/deadletter/{eid}/replay", headers=_h(slug, admin)
    )

    assert r.status_code == 200
    estados = dict(
        await conn.fetch("SELECT origin_group, status FROM evento_dead_letter WHERE event_id = $1", eid)
    )
    assert estados == {GROUP: "resolved", "workflow-service": "pending"}


async def test_replay_de_un_reclassified_obsoleto_no_baja_el_chunk(ctx) -> None:
    """Aparcado reclassified(1); el radicado volvió a 3. El replay NO puede bajarlo."""
    slug, conn, client = ctx
    await _indexar(conn, slug, 3)
    admin = await _usuario(conn, crud=3, clearance=3)
    eid = await _aparcar(
        conn, slug, "document.radicado.reclassified", extra={"nivel_nuevo": 1}
    )

    r = await client.post(
        f"/api/v1/knowledge/admin/deadletter/{eid}/replay", headers=_h(slug, admin)
    )

    assert r.status_code == 200, r.text
    nivel = await conn.fetchval(
        "SELECT nivel_seguridad FROM knowledge_chunk "
        "WHERE source_type = 'radicado' AND source_ref = $1",
        TRACKING,
    )
    assert nivel == 3


async def test_replay_sin_radicado_vivo_da_409_y_sigue_pendiente(ctx) -> None:
    slug, conn, client = ctx
    admin = await _usuario(conn, crud=3, clearance=3)
    eid = await _aparcar(
        conn, slug, "document.radicado.reclassified", extra={"nivel_nuevo": 1}
    )
    r = await client.post(
        f"/api/v1/knowledge/admin/deadletter/{eid}/replay", headers=_h(slug, admin)
    )
    # Sin radicado vivo el gate de clearance ya es fail-closed (404 neutro) o el ancla da 409.
    assert r.status_code in (404, 409)
    assert await conn.fetchval(
        "SELECT status FROM evento_dead_letter WHERE event_id = $1", eid
    ) == "pending"
