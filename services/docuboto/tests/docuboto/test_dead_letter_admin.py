# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""La consola única del dead-letter (ADR-021 Fase 5, ADR-049 §4).

Una copia de cada comportamiento: gate de permiso, listado sin contenido y con
total exacto, detalle con asiento solo si se autoriza, descarte con motivo
sustantivo y reproceso (404, 409, 422, 502) despachado al núcleo de cada grupo.
El SQL del repositorio y su gate de clearance en el ``WHERE`` se prueban contra
Postgres real en los carriles de integración.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from orpycamcp_common.database import get_tenant_conn
from orpycamcp_common.usuario import instalar_inyector

from docuboto.core.dead_letter import DeadLetterRepository, ReplayFallido
from docuboto.main import create_app
from docuboto.routers import admin_deadletter as consola

USER = uuid.UUID("11111111-1111-1111-1111-111111111111")
HEADERS = {"X-Tenant-Slug": "demo", "X-User-Id": str(USER)}
BASE = "/api/v1/admin/deadletter"
EVT = "eeeeeeee-0000-0000-0000-000000000001"
NOW = datetime(2026, 11, 27, 10, 0, tzinfo=UTC)

app = create_app((consola.router,), with_lifespan=False, with_ops=False, rate_limit=False)
instalar_inyector(app)


def _fila(*, tenant: str = "demo", grupo: str = "workflow-service", **kw) -> dict:
    fila = {
        "event_id": EVT,
        "event_type": "document.radicado.created",
        "correlation_id": "cccc-0001",
        "tenant_slug": tenant,
        "origin_stream": "orpycamcp.document.events",
        "origin_group": grupo,
        "status": "pending",
        "dead_lettered_at": NOW,
        "resolved_at": None,
        "payload": {"event_id": EVT, "tenant_slug": tenant, "payload": {"id": "r-1"}},
        "failure": {"dead_letter_reason": "max_deliveries_exceeded"},
    }
    fila.update(kw)
    return fila


@pytest_asyncio.fixture
async def cliente() -> AsyncGenerator[tuple[AsyncClient, AsyncMock], None]:
    conn = AsyncMock()
    conn.fetchval.return_value = True  # check_permission: autorizado
    conn.transaction = MagicMock(return_value=AsyncMock())

    async def _conn() -> AsyncGenerator[AsyncMock, None]:
        yield conn

    app.dependency_overrides[get_tenant_conn] = _conn
    with patch.object(consola, "resolve_caller_clearance", AsyncMock(return_value=3)):
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            yield ac, conn
    app.dependency_overrides.clear()


@pytest.fixture
def asiento():
    with patch.object(consola.audit, "append", AsyncMock()) as m:
        yield m


# ── gate y grupos ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sin_perm_dlq_admin_es_403(cliente) -> None:
    ac, conn = cliente
    conn.fetchval.return_value = None
    assert (await ac.get(f"{BASE}/workflow/", headers=HEADERS)).status_code == 403


@pytest.mark.asyncio
async def test_un_grupo_desconocido_es_404(cliente) -> None:
    ac, _ = cliente
    resp = await ac.get(f"{BASE}/mcp/", headers=HEADERS)
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "grupo_desconocido"


def test_los_cuatro_grupos_y_sus_reprocesos() -> None:
    assert set(consola.GRUPOS) == {"workflow", "notification", "signature", "inteligencia"}
    assert set(consola.REPLAYS) == {"workflow", "notification", "signature"}


# ── listado ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("grupo", sorted(consola.GRUPOS))
async def test_el_listado_filtra_por_su_grupo_y_da_el_total(cliente, grupo) -> None:
    ac, _ = cliente
    resumen = {k: v for k, v in _fila(grupo=consola.GRUPOS[grupo]).items()
               if k not in {"payload", "failure", "correlation_id", "tenant_slug"}}
    lista = AsyncMock(return_value=([resumen], 7))
    with patch.object(DeadLetterRepository, "list_visible", lista):
        resp = await ac.get(f"{BASE}/{grupo}/?status=pending&page=2&size=5", headers=HEADERS)
    assert resp.status_code == 200
    assert resp.headers["X-Total-Count"] == "7"
    args, kwargs = lista.await_args
    assert args == ("demo", consola.GRUPOS[grupo], 3)
    assert kwargs["offset"] == 5 and kwargs["limit"] == 5 and kwargs["status"] == "pending"
    item = resp.json()[0]
    for oculto in ("payload", "failure", "last_error", "tracking_number"):
        assert oculto not in item


# ── detalle ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_el_detalle_autorizado_deja_asiento(cliente, asiento) -> None:
    ac, _ = cliente
    with patch.object(DeadLetterRepository, "get_by_event_id", AsyncMock(return_value=_fila())):
        resp = await ac.get(f"{BASE}/workflow/{EVT}", headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json()["payload"]["tenant_slug"] == "demo"
    assert asiento.await_args.kwargs["action"] == "evento.dead_letter.viewed"


@pytest.mark.asyncio
async def test_lo_que_no_se_ve_es_404_y_no_se_asienta(cliente, asiento) -> None:
    ac, _ = cliente
    with patch.object(DeadLetterRepository, "get_by_event_id", AsyncMock(return_value=None)):
        resp = await ac.get(f"{BASE}/workflow/{EVT}", headers=HEADERS)
    assert resp.status_code == 404
    asiento.assert_not_awaited()


# ── descarte ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("motivo", ["", "corto", "   x        "])
async def test_el_descarte_exige_un_motivo_sustantivo(cliente, motivo) -> None:
    ac, _ = cliente
    resp = await ac.post(f"{BASE}/workflow/{EVT}/discard", json={"reason": motivo}, headers=HEADERS)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_descartar_cierra_y_asienta_el_motivo(cliente, asiento) -> None:
    ac, _ = cliente
    with (
        patch.object(DeadLetterRepository, "get_for_update", AsyncMock(return_value=_fila())),
        patch.object(DeadLetterRepository, "transition_status", AsyncMock(return_value=1)) as tr,
    ):
        resp = await ac.post(
            f"{BASE}/inteligencia/{EVT}/discard",
            json={"reason": "  proveedor retirado de la entidad  "},
            headers=HEADERS,
        )
    assert resp.status_code == 200
    assert resp.json()["new_status"] == "discarded"
    assert tr.await_args.args == (EVT, "demo", "discarded", consola.GRUPOS["inteligencia"])
    kw = asiento.await_args.kwargs
    assert kw["action"] == "evento.dead_letter.discarded"
    assert kw["payload"]["reason"] == "proveedor retirado de la entidad"


@pytest.mark.asyncio
async def test_descartar_lo_ya_cerrado_es_409(cliente, asiento) -> None:
    ac, _ = cliente
    with (
        patch.object(DeadLetterRepository, "get_for_update", AsyncMock(return_value=_fila())),
        patch.object(DeadLetterRepository, "transition_status", AsyncMock(return_value=0)),
    ):
        resp = await ac.post(
            f"{BASE}/workflow/{EVT}/discard", json={"reason": "motivo suficiente"}, headers=HEADERS
        )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "already_resolved"


# ── reproceso ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reprocesar_despacha_al_nucleo_del_grupo_y_resuelve(cliente, asiento) -> None:
    ac, conn = cliente
    nucleo = AsyncMock(return_value={"assigned_to": "Oficina Jurídica"})
    with (
        patch.dict(consola.REPLAYS, {"workflow": nucleo}),
        patch.object(DeadLetterRepository, "get_for_update", AsyncMock(return_value=_fila())),
        patch.object(DeadLetterRepository, "transition_status", AsyncMock(return_value=1)),
    ):
        resp = await ac.post(f"{BASE}/workflow/{EVT}/replay", headers=HEADERS)
    assert resp.status_code == 200
    assert resp.json()["outcome"] == {"rows_affected": 1, "assigned_to": "Oficina Jurídica"}
    args, kw = nucleo.await_args
    assert args == (conn, "demo")
    assert kw["event_id"] == EVT and kw["actor"] == str(USER)
    assert kw["origin_stream"] == "orpycamcp.document.events"
    assert kw["envelope"]["tenant_slug"] == "demo"
    assert asiento.await_args.kwargs["action"] == "evento.dead_letter.replayed"


@pytest.mark.asyncio
async def test_inteligencia_no_tiene_reproceso(cliente) -> None:
    ac, _ = cliente
    resp = await ac.post(f"{BASE}/inteligencia/{EVT}/replay", headers=HEADERS)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "replay_no_disponible"


@pytest.mark.asyncio
async def test_reprocesar_lo_que_no_se_ve_es_404(cliente) -> None:
    ac, _ = cliente
    with patch.object(DeadLetterRepository, "get_for_update", AsyncMock(return_value=None)):
        resp = await ac.post(f"{BASE}/workflow/{EVT}/replay", headers=HEADERS)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_un_sobre_de_otra_entidad_es_422(cliente) -> None:
    ac, _ = cliente
    fila = _fila()
    fila["payload"] = {"tenant_slug": "otra", "payload": {}}
    nucleo = AsyncMock()
    with (
        patch.dict(consola.REPLAYS, {"workflow": nucleo}),
        patch.object(DeadLetterRepository, "get_for_update", AsyncMock(return_value=fila)),
    ):
        resp = await ac.post(f"{BASE}/workflow/{EVT}/replay", headers=HEADERS)
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "tenant_mismatch"
    nucleo.assert_not_awaited()


@pytest.mark.asyncio
async def test_un_fallo_del_nucleo_es_502_generico_y_no_cierra(cliente) -> None:
    ac, _ = cliente
    with (
        patch.dict(consola.REPLAYS, {"workflow": AsyncMock(side_effect=RuntimeError("detalle interno"))}),
        patch.object(DeadLetterRepository, "get_for_update", AsyncMock(return_value=_fila())),
        patch.object(DeadLetterRepository, "transition_status", AsyncMock(return_value=1)) as tr,
    ):
        resp = await ac.post(f"{BASE}/workflow/{EVT}/replay", headers=HEADERS)
    assert resp.status_code == 502
    assert "detalle interno" not in resp.text
    tr.assert_not_awaited()


@pytest.mark.asyncio
async def test_replay_fallido_lleva_su_mensaje(cliente) -> None:
    ac, _ = cliente
    with (
        patch.dict(
            consola.REPLAYS,
            {"notification": AsyncMock(side_effect=ReplayFallido("1 de 2 envíos fallaron"))},
        ),
        patch.object(DeadLetterRepository, "get_for_update", AsyncMock(return_value=_fila())),
    ):
        resp = await ac.post(f"{BASE}/notification/{EVT}/replay", headers=HEADERS)
    assert resp.status_code == 502
    assert resp.json()["detail"]["detail"] == "1 de 2 envíos fallaron"


@pytest.mark.asyncio
async def test_reprocesar_lo_ya_resuelto_es_409(cliente, asiento) -> None:
    ac, _ = cliente
    with (
        patch.dict(consola.REPLAYS, {"signature": AsyncMock(return_value={})}),
        patch.object(DeadLetterRepository, "get_for_update", AsyncMock(return_value=_fila())),
        patch.object(DeadLetterRepository, "transition_status", AsyncMock(return_value=0)),
    ):
        resp = await ac.post(f"{BASE}/signature/{EVT}/replay", headers=HEADERS)
    assert resp.status_code == 409
    asiento.assert_not_awaited()
