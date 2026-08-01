# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del PINAR (RF-ADM-08, E14). Mockean get_tenant_conn (sin BD real).

Los invariantes de motor (índice único de un plan en ejecución, trigger append-only
de pinar_seguimiento, CHECK de impacto/avance) se validan contra Postgres REAL en la
migración 007 y no pueden probarse con mocks; aquí se cubren el ruteo, la máquina de
estados / editabilidad, la extracción del actor y el mapeo excepción→HTTP.
"""

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.database import get_tenant_conn
from app.main import app

# El actor imputable llega en X-User-Id; los actos de ciclo de vida lo exigen.
_HDRS = {"X-User-Id": str(uuid.uuid4()), "X-Tenant-Slug": "demo"}


def _plan_row(*, estado: str = "borrador", version: int = 1, plan_id: uuid.UUID | None = None) -> dict:
    now = datetime.now(tz=UTC)
    return {
        "id": plan_id or uuid.uuid4(),
        "version": version,
        "vigencia_desde": None,
        "vigencia_hasta": None,
        "vision": "Modernizar la gestión documental",
        "acto_administrativo": None,
        "estado": estado,
        "aprobado_por": None,
        "aprobado_at": None,
        "created_at": now,
    }


@asynccontextmanager
async def _noop_tx() -> AsyncGenerator[None, None]:
    yield


@pytest_asyncio.fixture(scope="function")
async def client_conn() -> AsyncGenerator[tuple[AsyncClient, AsyncMock], None]:
    conn = AsyncMock()
    conn.fetch.return_value = []
    conn.fetchrow.return_value = None
    # Las escrituras del PINAR ahora exigen USUA_PERM_ADMIN (app/core/authz.py),
    # gate resuelto vía conn.fetchval — truthy por defecto (permiso concedido) para
    # no romper las aserciones de negocio de los tests existentes; los tests que SÍ
    # ejercen fetchval con fines de negocio (p. ej. max_version) lo sobreescriben.
    conn.fetchval.return_value = True
    conn.execute.return_value = None
    # `async with conn.transaction()` debe ser un CM async REENTRANTE (lección E21).
    conn.transaction = MagicMock(side_effect=lambda *a, **k: _noop_tx())

    async def override() -> AsyncGenerator[AsyncMock, None]:
        yield conn

    app.dependency_overrides[get_tenant_conn] = override
    # La cadena hash de audit_log (E08) se prueba en orpycamcp_common; aquí basta
    # verificar QUE se llama al asiento inmutable, sin ejercer el hash real.
    with patch(
        "app.services.pinar_service.audit.append", new=AsyncMock(return_value="h")
    ) as audit_append:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            ac.pinar_audit = audit_append  # type: ignore[attr-defined]
            yield ac, conn
    app.dependency_overrides.clear()


# ── Ejes ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_ejes(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetch.return_value = [
        {"code": 1, "nombre": "Administración de archivos"},
        {"code": 3, "nombre": "Preservación de la información"},
    ]
    resp = await client.get("/api/v1/pinar/ejes")
    assert resp.status_code == 200
    assert {e["code"] for e in resp.json()} == {1, 3}


# ── Plan: creación y versionado ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_plan_bumps_version_and_audits(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, conn = client_conn
    conn.fetchval.return_value = 4  # max_version existente
    conn.fetchrow.return_value = _plan_row(version=5)
    resp = await client.post(
        "/api/v1/pinar/planes",
        json={"vision": "Modernizar"},
        headers=_HDRS,
    )
    assert resp.status_code == 201
    assert resp.json()["version"] == 5
    assert resp.json()["estado"] == "borrador"
    # se emitió el asiento en la audit_log INMUTABLE (E08), no en admin_audit
    client.pinar_audit.assert_awaited()
    assert client.pinar_audit.await_args.kwargs["action"] == "pinar.plan_creado"


@pytest.mark.asyncio
async def test_create_plan_without_identity_is_forbidden(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Sin X-User-Id el gate RBAC (USUA_PERM_ADMIN) rechaza con 403 ANTES de
    llegar al router — `_require_actor` (400 actor_requerido, defensa en
    profundidad para el acto imputable) queda así estructuralmente inalcanzable
    por esta vía: no hay forma de demostrar el permiso sin X-User-Id, así que
    la ausencia de actor nunca llega a la capa de negocio."""
    client, conn = client_conn
    conn.fetchrow.return_value = _plan_row()
    resp = await client.post("/api/v1/pinar/planes", json={"vision": "x"})
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_create_plan_forbidden_without_permission(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """403 con identidad presente pero sin USUA_PERM_ADMIN (gate denegado)."""
    client, conn = client_conn
    conn.fetchval.return_value = False  # permiso denegado
    conn.fetchrow.return_value = _plan_row()
    resp = await client.post("/api/v1/pinar/planes", json={"vision": "x"}, headers=_HDRS)
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_get_plan_not_found(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetchrow.return_value = None
    resp = await client.get(f"/api/v1/pinar/planes/{uuid.uuid4()}")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "not_found"


# ── Plan: máquina de estados ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_plan_only_in_borrador(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetchrow.return_value = _plan_row(estado="aprobado")
    resp = await client.patch(
        f"/api/v1/pinar/planes/{uuid.uuid4()}", json={"vision": "x"}, headers=_HDRS
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "plan_no_borrador"


@pytest.mark.asyncio
async def test_aprobar_requires_acto(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, _ = client_conn
    # acto_administrativo vacío → 422 del schema (con permiso concedido, para
    # llegar hasta la validación de negocio en vez de quedarse en el gate).
    resp = await client.post(
        f"/api/v1/pinar/planes/{uuid.uuid4()}/aprobar",
        json={"acto_administrativo": ""},
        headers=_HDRS,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_aprobar_ok(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    pid = uuid.uuid4()
    # get_plan (existe borrador) → set_estado (devuelve aprobado)
    conn.fetchrow.side_effect = [
        _plan_row(estado="borrador", plan_id=pid),
        _plan_row(estado="aprobado", plan_id=pid),
    ]
    resp = await client.post(
        f"/api/v1/pinar/planes/{pid}/aprobar",
        json={"acto_administrativo": "Resolución 123 de 2026"},
        headers=_HDRS,
    )
    assert resp.status_code == 200
    assert resp.json()["estado"] == "aprobado"
    assert client.pinar_audit.await_args.kwargs["action"] == "pinar.plan_aprobado"


@pytest.mark.asyncio
async def test_aprobar_no_borrador_conflict(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    pid = uuid.uuid4()
    # get_plan (existe) → set_estado devuelve None (no estaba en borrador)
    conn.fetchrow.side_effect = [_plan_row(estado="aprobado", plan_id=pid), None]
    resp = await client.post(
        f"/api/v1/pinar/planes/{pid}/aprobar",
        json={"acto_administrativo": "Resolución 123"},
        headers=_HDRS,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "transicion_invalida"


@pytest.mark.asyncio
async def test_ejecutar_unico_en_ejecucion(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    """Invariante 1: si el índice único rechaza el 2o en_ejecucion → 409 traducido."""
    client, conn = client_conn
    pid = uuid.uuid4()
    # get_plan usa fetchrow; set_estado también → el 2o fetchrow (set_estado) lanza
    # la violación de unicidad del índice parcial (ya hay un plan en_ejecucion).
    conn.fetchrow.side_effect = [
        _plan_row(estado="aprobado", plan_id=pid),
        asyncpg.UniqueViolationError("dup en_ejecucion"),
    ]
    resp = await client.post(f"/api/v1/pinar/planes/{pid}/ejecutar", headers=_HDRS)
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "plan_en_ejecucion_existe"


# ── Editabilidad estructural por estado ───────────────────────────────────────


@pytest.mark.asyncio
async def test_create_aspecto_blocked_when_en_ejecucion(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, conn = client_conn
    conn.fetchrow.return_value = _plan_row(estado="en_ejecucion")
    resp = await client.post(
        f"/api/v1/pinar/planes/{uuid.uuid4()}/aspectos",
        json={"descripcion": "Sin política de preservación"},
        headers=_HDRS,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "plan_no_editable"


@pytest.mark.asyncio
async def test_create_aspecto_blocked_when_aprobado(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """#10: tras aprobar por acto administrativo, el contenido se CONGELA — no se
    editan aspectos/objetivos/proyectos (correspondencia acto↔contenido)."""
    client, conn = client_conn
    conn.fetchrow.return_value = _plan_row(estado="aprobado")
    resp = await client.post(
        f"/api/v1/pinar/planes/{uuid.uuid4()}/aspectos",
        json={"descripcion": "tardío"},
        headers=_HDRS,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "plan_no_editable"


@pytest.mark.asyncio
async def test_create_aspecto_ok_in_borrador(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    pid = uuid.uuid4()
    aid = uuid.uuid4()
    conn.fetchrow.side_effect = [
        _plan_row(estado="borrador", plan_id=pid),  # _plan_or_404
        {"id": aid, "plan_id": pid, "descripcion": "x", "riesgo": None, "orden": 0},  # insert
    ]
    resp = await client.post(
        f"/api/v1/pinar/planes/{pid}/aspectos", json={"descripcion": "x"}, headers=_HDRS
    )
    assert resp.status_code == 201
    assert resp.json()["id"] == str(aid)


# ── Seguimiento: trackable solo en aprobado/en_ejecucion ──────────────────────


@pytest.mark.asyncio
async def test_seguimiento_blocked_in_borrador(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    pid = uuid.uuid4()
    prid = uuid.uuid4()
    proy = {
        "id": prid, "plan_id": pid, "objetivo_id": None, "nombre": "p", "meta": None,
        "indicador": None, "responsable": None, "recurso": None, "fecha_inicio": None,
        "fecha_fin": None, "estado": "planeado", "avance_pct": 0,
    }
    # get_proyecto → get_plan(borrador)
    conn.fetchrow.side_effect = [proy, _plan_row(estado="borrador", plan_id=pid)]
    resp = await client.post(
        f"/api/v1/pinar/proyectos/{prid}/seguimiento",
        json={"fecha": "2026-07-21", "avance_pct": 30},
        headers=_HDRS,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "plan_no_seguible"


@pytest.mark.asyncio
async def test_seguimiento_ok_and_projects_avance(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, conn = client_conn
    pid = uuid.uuid4()
    prid = uuid.uuid4()
    sid = uuid.uuid4()
    proy = {
        "id": prid, "plan_id": pid, "objetivo_id": None, "nombre": "p", "meta": None,
        "indicador": None, "responsable": None, "recurso": None, "fecha_inicio": None,
        "fecha_fin": None, "estado": "en_curso", "avance_pct": 0,
    }
    seg = {
        "id": sid, "proyecto_id": prid, "fecha": "2026-07-21", "avance_pct": 55,
        "observacion": None, "registrado_por": None, "created_at": datetime.now(tz=UTC),
    }
    conn.fetchrow.side_effect = [
        proy,  # get_proyecto
        _plan_row(estado="en_ejecucion", plan_id=pid),  # _plan_or_404
        seg,  # insert seguimiento
    ]
    resp = await client.post(
        f"/api/v1/pinar/proyectos/{prid}/seguimiento",
        json={"fecha": "2026-07-21", "avance_pct": 55},
        headers=_HDRS,
    )
    assert resp.status_code == 201
    assert resp.json()["avance_pct"] == 55
    # se proyectó el avance del proyecto (UPDATE pinar_proyecto SET avance_pct)
    assert any(
        "UPDATE pinar_proyecto SET avance_pct" in str(c.args[0])
        for c in conn.execute.call_args_list
    )


# ── Priorización: parsing del resultado agregado ──────────────────────────────


@pytest.mark.asyncio
async def test_get_priorizacion_parses_impactos(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, conn = client_conn
    pid = uuid.uuid4()
    aid = uuid.uuid4()
    conn.fetchrow.return_value = _plan_row(estado="aprobado", plan_id=pid)  # _plan_or_404
    conn.fetch.return_value = [
        {
            "aspecto_id": aid,
            "descripcion": "Sin backup",
            "prioridad": 17,
            "impactos": '{"3": 9, "4": 8}',  # jsonb como str (asyncpg puede devolver texto)
        }
    ]
    resp = await client.get(f"/api/v1/pinar/planes/{pid}/priorizacion")
    assert resp.status_code == 200
    body = resp.json()[0]
    assert body["prioridad"] == 17
    assert body["impactos"] == {"3": 9, "4": 8}


# ── Articulación: validación de tipo de instrumento ───────────────────────────


@pytest.mark.asyncio
async def test_instrumento_tipo_invalido_422(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, _ = client_conn
    resp = await client.post(
        f"/api/v1/pinar/planes/{uuid.uuid4()}/instrumentos",
        json={"tipo": "inventado", "ref": "TRD-2026"},
        headers=_HDRS,
    )
    assert resp.status_code == 422
