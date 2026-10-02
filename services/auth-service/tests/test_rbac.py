# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del RBAC (E08): autorización pura + endpoints con conexión mockeada."""

import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
from httpx import AsyncClient

from app.core.authz import Caller, get_caller
from app.core.database import get_tenant_conn
from app.main import app
from app.services import rbac_service as rbac_service_module
from app.services.rbac_service import has_permission

TENANT_HEADERS = {"X-Tenant-Slug": "demo"}


class _FakeTx:
    """Context manager async no-op para simular conn.transaction()."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> bool:
        return False
# El llamante de las pruebas administra RBAC (USUA_PERM_ADMIN); el gate (ADR-013) se
# resuelve normalmente vía BD, pero aquí se sobreescribe para aislar el endpoint.
_ADMIN_CALLER = Caller(
    user_id=uuid.uuid4(),
    keycloak_sub="kc-admin",
    is_root=True,
    permissions={"USUA_PERM_ADMIN": 5},
)
# Un administrador de entidad SIN ROOT: tiene USUA_PERM_ADMIN (pasa el gate del
# router) pero no debe poder auto-escalarse a ROOT (hallazgo Alto cerrado).
_NONROOT_ADMIN_CALLER = Caller(
    user_id=uuid.uuid4(),
    keycloak_sub="kc-admin-nonroot",
    is_root=False,
    permissions={"USUA_PERM_ADMIN": 5},
)


# --- autorización pura (sin BD) --------------------------------------------
def test_has_permission_root_bypass() -> None:
    perms = {"USUA_PERM_ROOT": 5}
    assert has_permission(perms, "PERM_RADI", min_crud=3) is True


def test_has_permission_min_crud() -> None:
    perms = {"PERM_RADI": 2}
    assert has_permission(perms, "PERM_RADI", min_crud=2) is True
    assert has_permission(perms, "PERM_RADI", min_crud=3) is False


def test_has_permission_absent() -> None:
    assert has_permission({}, "PERM_RADI") is False


# --- endpoints con conexión mockeada ---------------------------------------
def _override_conn(conn: AsyncMock) -> None:
    async def _dep() -> AsyncGenerator[AsyncMock, None]:
        yield conn

    # orpycamcp_common.audit.append() envuelve el INSERT en `async with
    # conn.transaction()` — todas las escrituras RBAC ahora auditan, así que el
    # mock necesita soportar el protocolo de contexto asíncrono por defecto.
    conn.transaction = MagicMock(return_value=_FakeTx())
    app.dependency_overrides[get_tenant_conn] = _dep
    # El gate de autorización se aísla: el endpoint queda probado sin la resolución
    # de permisos del llamante (cubierta por sus propios tests).
    app.dependency_overrides[get_caller] = lambda: _ADMIN_CALLER


@pytest.mark.asyncio
async def test_create_user(client: AsyncClient) -> None:
    new_id = uuid.uuid4()
    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "id": new_id,
        "username": "JPEREZ",
        "email": "j@x.co",
        "keycloak_sub": None,
        "is_root": False,
        "is_active": True,
    }
    _override_conn(conn)

    resp = await client.post(
        "/api/v1/auth/users",
        json={"username": "jperez", "email": "j@x.co"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["username"] == "JPEREZ"
    assert body["id"] == str(new_id)


@pytest.mark.asyncio
async def test_missing_tenant_header_is_400(client: AsyncClient) -> None:
    # Sin override: get_tenant_conn valida la cabecera y debe rechazar.
    app.dependency_overrides.pop(get_tenant_conn, None)
    resp = await client.get("/api/v1/auth/groups")  # sin X-Tenant-Slug
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "invalid_tenant"


@pytest.mark.asyncio
async def test_list_permissions(client: AsyncClient) -> None:
    conn = AsyncMock()
    conn.fetch.return_value = [
        {"id": uuid.uuid4(), "nombre": "PERM_RADI", "descripcion": "base"},
        {"id": uuid.uuid4(), "nombre": "USUA_PERM_ROOT", "descripcion": "root"},
    ]
    _override_conn(conn)

    resp = await client.get("/api/v1/auth/permissions", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    nombres = [p["nombre"] for p in resp.json()]
    assert "PERM_RADI" in nombres and "USUA_PERM_ROOT" in nombres


@pytest.mark.asyncio
async def test_user_permissions_resolution(client: AsyncClient) -> None:
    uid = uuid.uuid4()
    conn = AsyncMock()
    # get_user → existe; resolve_permissions → fetch; is_root → fetchval
    conn.fetchrow.return_value = {
        "id": uid,
        "username": "JPEREZ",
        "email": None,
        "keycloak_sub": None,
        "is_root": False,
        "is_active": True,
    }
    conn.fetch.return_value = [
        {"nombre": "PERM_RADI", "crud": 3},
        {"nombre": "USUA_PERM_EXPEDIENTE", "crud": 2},
    ]
    conn.fetchval.return_value = False  # is_root
    _override_conn(conn)

    resp = await client.get(f"/api/v1/auth/users/{uid}/permissions", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_root"] is False
    assert body["permissions"]["PERM_RADI"] == 3
    assert body["permissions"]["USUA_PERM_EXPEDIENTE"] == 2


# --- D-10: endpoint ligero para selectores (PERM_RADI, no admin) ------------
def _override_caller(conn: AsyncMock, caller: Caller) -> None:
    async def _dep() -> AsyncGenerator[AsyncMock, None]:
        yield conn

    app.dependency_overrides[get_tenant_conn] = _dep
    app.dependency_overrides[get_caller] = lambda: caller


@pytest.mark.asyncio
async def test_list_pickable_users_perm_radi(client: AsyncClient) -> None:
    """Un operador con PERM_RADI (sin admin) obtiene id+username; sin email/PII."""
    u1, u2 = uuid.uuid4(), uuid.uuid4()
    conn = AsyncMock()
    conn.fetch.return_value = [
        {"id": u1, "username": "ANA"},
        {"id": u2, "username": "BETO"},
    ]
    _override_caller(
        conn,
        Caller(user_id=u1, keycloak_sub="kc-op", is_root=False, permissions={"PERM_RADI": 3}),
    )
    resp = await client.get(
        "/api/v1/auth/users/pickable", headers={**TENANT_HEADERS, "X-User-Id": "kc-op"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert [u["username"] for u in body] == ["ANA", "BETO"]
    # Proyección mínima: solo id + username, sin email ni metadatos administrativos.
    assert set(body[0].keys()) == {"id", "username"}


@pytest.mark.asyncio
async def test_pickable_dice_cuantos_usuarios_hay_en_total(client: AsyncClient) -> None:
    """`X-Total-Count` lleva los usuarios ACTIVOS que hay, no los que caben.

    Este endpoint no pagina: recorta con un `LIMIT` y ya está. Sin la cabecera,
    quien no entrara en la ventana no existía para quien busca — y en un
    selector eso no se lee como una lista corta, se lee como que esa persona no
    está en el sistema. La cabecera es lo único que permite a la pantalla decir
    «hay más» en vez de mentir por omisión.
    """
    u1 = uuid.uuid4()
    conn = AsyncMock()
    conn.fetch.return_value = [{"id": u1, "username": "ANA"}]
    conn.fetchval.return_value = 940  # activos en el tenant
    _override_caller(
        conn,
        Caller(user_id=u1, keycloak_sub="kc-op", is_root=False, permissions={"PERM_RADI": 3}),
    )

    resp = await client.get(
        "/api/v1/auth/users/pickable", headers={**TENANT_HEADERS, "X-User-Id": "kc-op"}
    )

    assert resp.status_code == 200
    assert len(resp.json()) == 1
    # Lo que hay, no lo que cupo: son números distintos y por eso sirve.
    assert resp.headers["X-Total-Count"] == "940"


@pytest.mark.asyncio
async def test_pickable_cuenta_solo_los_activos(client: AsyncClient) -> None:
    """Control positivo: el total se calcula con el MISMO filtro que la lista.

    Un total que contara también los inactivos anunciaría que faltan personas
    elegibles cuando no falta ninguna — un aviso que cansa es un aviso que se
    ignora.
    """
    u1 = uuid.uuid4()
    conn = AsyncMock()
    conn.fetch.return_value = [{"id": u1, "username": "ANA"}]
    conn.fetchval.return_value = 1
    _override_caller(
        conn,
        Caller(user_id=u1, keycloak_sub="kc-op", is_root=False, permissions={"PERM_RADI": 3}),
    )

    await client.get(
        "/api/v1/auth/users/pickable", headers={**TENANT_HEADERS, "X-User-Id": "kc-op"}
    )

    sql_total = conn.fetchval.await_args[0][0]
    assert "is_active = true" in sql_total


# --- lectura de membresías/permisos/clearance de grupo ----------------------
@pytest.mark.asyncio
async def test_list_group_members(client: AsyncClient) -> None:
    group_id = uuid.uuid4()
    u1, u2 = uuid.uuid4(), uuid.uuid4()
    conn = AsyncMock()
    conn.fetchrow.return_value = {"id": group_id, "name": "ADMINS", "description": None}
    conn.fetch.return_value = [
        {
            "id": u1,
            "username": "ANA",
            "email": "ana@x.co",
            "keycloak_sub": None,
            "is_root": False,
            "is_active": True,
        },
        {
            "id": u2,
            "username": "BETO",
            "email": None,
            "keycloak_sub": None,
            "is_root": False,
            "is_active": True,
        },
    ]
    conn.fetchval.return_value = 2
    _override_conn(conn)

    resp = await client.get(f"/api/v1/auth/groups/{group_id}/members", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert [u["username"] for u in body] == ["ANA", "BETO"]
    assert resp.headers["X-Total-Count"] == "2"


@pytest.mark.asyncio
async def test_list_group_members_group_not_found(client: AsyncClient) -> None:
    conn = AsyncMock()
    conn.fetchrow.return_value = None  # get_group → no existe en este schema
    _override_conn(conn)

    resp = await client.get(f"/api/v1/auth/groups/{uuid.uuid4()}/members", headers=TENANT_HEADERS)
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "not_found"


@pytest.mark.asyncio
async def test_list_group_members_other_tenant_group_is_404(client: AsyncClient) -> None:
    """Un group_id que pertenece a OTRO tenant nunca existe en este search_path:
    get_group() devuelve None igual que un id inexistente — 404 indistinguible,
    nunca 403 (no debe confirmarse la existencia del grupo ajeno)."""
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    _override_conn(conn)

    resp = await client.get(f"/api/v1/auth/groups/{uuid.uuid4()}/members", headers=TENANT_HEADERS)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_group_members_denied_without_admin(client: AsyncClient) -> None:
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_FakeTx())  # append_denial() best-effort
    _override_caller(
        conn,
        Caller(
            user_id=uuid.uuid4(),
            keycloak_sub="kc-op",
            is_root=False,
            permissions={"PERM_RADI": 3},
        ),
    )
    resp = await client.get(f"/api/v1/auth/groups/{uuid.uuid4()}/members", headers=TENANT_HEADERS)
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_list_group_permissions(client: AsyncClient) -> None:
    group_id = uuid.uuid4()
    perm_id = uuid.uuid4()
    conn = AsyncMock()
    conn.fetchrow.return_value = {"id": group_id, "name": "ADMINS", "description": None}
    conn.fetch.return_value = [
        {"permission_id": perm_id, "nombre": "PERM_RADI", "descripcion": "base", "crud": 3},
    ]
    _override_conn(conn)

    resp = await client.get(f"/api/v1/auth/groups/{group_id}/permissions", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body == [
        {
            "permission_id": str(perm_id),
            "nombre": "PERM_RADI",
            "descripcion": "base",
            "crud": 3,
        }
    ]


@pytest.mark.asyncio
async def test_list_group_permissions_group_not_found(client: AsyncClient) -> None:
    conn = AsyncMock()
    conn.fetchrow.return_value = None
    _override_conn(conn)

    resp = await client.get(
        f"/api/v1/auth/groups/{uuid.uuid4()}/permissions", headers=TENANT_HEADERS
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_list_group_permissions_denied_without_admin(client: AsyncClient) -> None:
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_FakeTx())
    _override_caller(
        conn,
        Caller(
            user_id=uuid.uuid4(),
            keycloak_sub="kc-op",
            is_root=False,
            permissions={"PERM_RADI": 3},
        ),
    )
    resp = await client.get(
        f"/api/v1/auth/groups/{uuid.uuid4()}/permissions", headers=TENANT_HEADERS
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_list_pickable_users_denies_without_perm_radi(client: AsyncClient) -> None:
    """Sin PERM_RADI (ni root), el endpoint pickable responde 403."""
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_FakeTx())  # append_denial() best-effort
    _override_caller(
        conn,
        Caller(
            user_id=uuid.uuid4(),
            keycloak_sub="kc-nobody",
            is_root=False,
            permissions={"USUA_PERM_CONSULTA": 1},
        ),
    )
    resp = await client.get(
        "/api/v1/auth/users/pickable", headers={**TENANT_HEADERS, "X-User-Id": "kc-nobody"}
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


# --- gate de autorización (ADR-013) ----------------------------------------
@pytest.mark.asyncio
async def test_gate_denies_without_admin(client: AsyncClient) -> None:
    """Un llamante sin USUA_PERM_ADMIN (ni root) recibe 403."""
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_FakeTx())  # append_denial() best-effort
    _override_conn(conn)
    # Sobreescribe el llamante con uno sin permiso de administración.
    app.dependency_overrides[get_caller] = lambda: Caller(
        user_id=uuid.uuid4(), keycloak_sub="kc-op", is_root=False, permissions={"PERM_RADI": 3}
    )
    resp = await client.post(
        "/api/v1/auth/users",
        json={"username": "x", "email": "x@x.co"},
        headers={**TENANT_HEADERS, "X-User-Id": "kc-op"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_gate_missing_user_id_is_401(client: AsyncClient) -> None:
    """Sin X-User-Id el gate no puede resolver al llamante → 401."""
    conn = AsyncMock()

    async def _dep() -> AsyncGenerator[AsyncMock, None]:
        yield conn

    app.dependency_overrides[get_tenant_conn] = _dep  # tenant OK; get_caller real
    resp = await client.get("/api/v1/auth/groups", headers=TENANT_HEADERS)
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_gate_not_provisioned_is_403(client: AsyncClient) -> None:
    """UUID válido en X-User-Id pero sin fila en auth_users → 403 (no provisionado).

    X-User-Id es ahora el auth_users.id interno (UUID), NO el keycloak_sub.
    Un UUID no encontrado en la BD significa que el usuario no está aprovisionado.
    """
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_FakeTx())  # append_denial() best-effort
    conn.fetchrow.return_value = None  # get_user(UUID) → no row

    async def _dep() -> AsyncGenerator[AsyncMock, None]:
        yield conn

    app.dependency_overrides[get_tenant_conn] = _dep
    ghost_uuid = str(uuid.uuid4())   # valid UUID but not in auth_users
    resp = await client.get(
        "/api/v1/auth/groups", headers={**TENANT_HEADERS, "X-User-Id": ghost_uuid}
    )
    assert resp.status_code == 403


# --- Canal A: auditoría inmutable de escrituras RBAC ------------------------
# Las escrituras administrativas (crear usuario/grupo, membresías, permisos de
# grupo) dejan un asiento en `public.audit_log` DENTRO de la misma transacción
# que la mutación (no best-effort). `_audit.append` se mockea para aislar el
# endpoint del hasheo/encadenamiento real (cubierto por `shared/tests/test_audit.py`).


def _patch_audit(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock_append = AsyncMock(return_value="fake-hash")
    monkeypatch.setattr(rbac_service_module, "_audit", MagicMock(append=mock_append))
    monkeypatch.setattr(rbac_service_module, "_AUDIT_AVAILABLE", True)
    return mock_append


@pytest.mark.asyncio
async def test_create_user_audits_action(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_append = _patch_audit(monkeypatch)
    new_id = uuid.uuid4()
    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "id": new_id,
        "username": "JPEREZ",
        "email": "j@x.co",
        "keycloak_sub": None,
        "is_root": False,
        "is_active": True,
    }
    _override_conn(conn)

    resp = await client.post(
        "/api/v1/auth/users",
        json={"username": "jperez", "email": "j@x.co"},
        headers={**TENANT_HEADERS, "X-User-Id": str(_ADMIN_CALLER.user_id)},
    )
    assert resp.status_code == 201
    mock_append.assert_awaited_once()
    kwargs = mock_append.call_args.kwargs
    assert kwargs["action"] == "rbac.usuario_creado"
    assert kwargs["tenant_slug"] == "demo"
    assert kwargs["actor"] == str(_ADMIN_CALLER.user_id)
    assert kwargs["object_type"] == "usuario"
    assert kwargs["object_ref"] == str(new_id)


@pytest.mark.asyncio
async def test_create_user_failure_leaves_no_audit_entry(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Atomicidad (mitad 1): si la mutación falla, no se llega a auditar."""
    mock_append = _patch_audit(monkeypatch)
    conn = AsyncMock()
    conn.fetchrow.side_effect = asyncpg.UniqueViolationError("dup")
    _override_conn(conn)

    resp = await client.post(
        "/api/v1/auth/users",
        json={"username": "jperez", "email": "j@x.co"},
        headers={**TENANT_HEADERS, "X-User-Id": str(_ADMIN_CALLER.user_id)},
    )
    assert resp.status_code == 409
    mock_append.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_user_audit_failure_reverts_mutation(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Atomicidad (mitad 2): si la auditoría falla, la mutación no queda
    confirmada — la excepción de `_audit.append` propaga fuera de
    `async with conn.transaction()` y el endpoint responde error, no 201."""
    mock_append = AsyncMock(side_effect=RuntimeError("audit backend down"))
    monkeypatch.setattr(rbac_service_module, "_audit", MagicMock(append=mock_append))
    monkeypatch.setattr(rbac_service_module, "_AUDIT_AVAILABLE", True)

    new_id = uuid.uuid4()
    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "id": new_id,
        "username": "JPEREZ",
        "email": "j@x.co",
        "keycloak_sub": None,
        "is_root": False,
        "is_active": True,
    }
    _override_conn(conn)

    # La mutación NO se confirma como éxito: la excepción de la auditoría
    # propaga sin capturarse (ASGITransport re-lanza los errores 500 no
    # manejados) y `conn.transaction()` (real en producción) revierte el
    # INSERT del usuario — nunca llega a un 201.
    with pytest.raises(RuntimeError, match="audit backend down"):
        await client.post(
            "/api/v1/auth/users",
            json={"username": "jperez", "email": "j@x.co"},
            headers={**TENANT_HEADERS, "X-User-Id": str(_ADMIN_CALLER.user_id)},
        )
    mock_append.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_user_without_tenant_slug_fails_closed(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-closed: sin tenant_slug la escritura se rehúsa en vez de imputar
    el asiento al tenant del sistema (ver `_audit_tenant`)."""
    mock_append = _patch_audit(monkeypatch)
    conn = AsyncMock()
    conn.fetchrow.return_value = {
        "id": uuid.uuid4(),
        "username": "JPEREZ",
        "email": None,
        "keycloak_sub": None,
        "is_root": False,
        "is_active": True,
    }
    _override_conn(conn)

    # request.headers.get("X-Tenant-Slug") resuelve "demo" vía get_tenant_conn
    # (exigido para llegar al handler); se fuerza el caso límite invocando el
    # servicio directamente para probar el guard fail-closed en aislamiento.
    from app.services.rbac_service import RBACService

    service = RBACService(conn)
    with pytest.raises(RuntimeError, match="tenant_slug"):
        await service.create_user(
            username="jperez",
            email=None,
            keycloak_sub=None,
            is_root=False,
            tenant_slug=None,
            actor=str(_ADMIN_CALLER.user_id),
            caller_is_root=True,
        )
    mock_append.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_group_audits_action(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_append = _patch_audit(monkeypatch)
    group_id = uuid.uuid4()
    conn = AsyncMock()
    conn.fetchrow.return_value = {"id": group_id, "name": "ADMINS", "description": None}
    _override_conn(conn)

    resp = await client.post(
        "/api/v1/auth/groups",
        json={"name": "ADMINS"},
        headers={**TENANT_HEADERS, "X-User-Id": str(_ADMIN_CALLER.user_id)},
    )
    assert resp.status_code == 201
    mock_append.assert_awaited_once()
    kwargs = mock_append.call_args.kwargs
    assert kwargs["action"] == "rbac.grupo_creado"
    assert kwargs["object_type"] == "grupo"
    assert kwargs["object_ref"] == str(group_id)


@pytest.mark.asyncio
async def test_add_member_audits_action(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_append = _patch_audit(monkeypatch)
    conn = AsyncMock()
    _override_conn(conn)
    group_id, user_id = uuid.uuid4(), uuid.uuid4()

    resp = await client.post(
        f"/api/v1/auth/groups/{group_id}/members/{user_id}",
        headers={**TENANT_HEADERS, "X-User-Id": str(_ADMIN_CALLER.user_id)},
    )
    assert resp.status_code == 201
    mock_append.assert_awaited_once()
    kwargs = mock_append.call_args.kwargs
    assert kwargs["action"] == "rbac.miembro_agregado"
    assert kwargs["payload"] == {"group_id": str(group_id), "user_id": str(user_id)}


@pytest.mark.asyncio
async def test_add_member_failure_leaves_no_audit_entry(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_append = _patch_audit(monkeypatch)
    conn = AsyncMock()
    conn.fetchval.side_effect = asyncpg.ForeignKeyViolationError("fk")
    _override_conn(conn)

    resp = await client.post(
        f"/api/v1/auth/groups/{uuid.uuid4()}/members/{uuid.uuid4()}",
        headers={**TENANT_HEADERS, "X-User-Id": str(_ADMIN_CALLER.user_id)},
    )
    assert resp.status_code == 404
    mock_append.assert_not_awaited()


@pytest.mark.asyncio
async def test_remove_member_audits_action(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_append = _patch_audit(monkeypatch)
    conn = AsyncMock()
    _override_conn(conn)
    group_id, user_id = uuid.uuid4(), uuid.uuid4()

    resp = await client.delete(
        f"/api/v1/auth/groups/{group_id}/members/{user_id}",
        headers={**TENANT_HEADERS, "X-User-Id": str(_ADMIN_CALLER.user_id)},
    )
    assert resp.status_code == 204
    mock_append.assert_awaited_once()
    assert mock_append.call_args.kwargs["action"] == "rbac.miembro_removido"


@pytest.mark.asyncio
async def test_set_group_permission_audits_action(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_append = _patch_audit(monkeypatch)
    conn = AsyncMock()
    _override_conn(conn)
    group_id, perm_id = uuid.uuid4(), uuid.uuid4()

    resp = await client.put(
        f"/api/v1/auth/groups/{group_id}/permissions/{perm_id}",
        json={"crud": 3},
        headers={**TENANT_HEADERS, "X-User-Id": str(_ADMIN_CALLER.user_id)},
    )
    assert resp.status_code == 200
    mock_append.assert_awaited_once()
    kwargs = mock_append.call_args.kwargs
    assert kwargs["action"] == "rbac.permiso_grupo_asignado"
    assert kwargs["payload"]["crud"] == 3


@pytest.mark.asyncio
async def test_rbac_write_500_when_audit_lib_unavailable(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Fail-closed: sin la librería de auditoría instalada, la escritura se
    rehúsa (500) en vez de saltarse el asiento en silencio."""
    monkeypatch.setattr(rbac_service_module, "_AUDIT_AVAILABLE", False)
    conn = AsyncMock()
    _override_conn(conn)

    resp = await client.post(
        f"/api/v1/auth/groups/{uuid.uuid4()}/members/{uuid.uuid4()}",
        headers={**TENANT_HEADERS, "X-User-Id": str(_ADMIN_CALLER.user_id)},
    )
    assert resp.status_code == 500
    assert resp.json()["detail"]["error"] == "auditoria_no_disponible"


# --- Cierre del hallazgo Alto: auto-escalada a USUA_PERM_ROOT ---------------
# Dos vías: crear un usuario con is_root=true, y conceder USUA_PERM_ROOT al
# grupo del que el propio actor es miembro. Ambas exigen que el LLAMANTE ya
# sea ROOT; un USUA_PERM_ADMIN no-ROOT recibe 403 y no deja asiento de éxito.


@pytest.mark.asyncio
async def test_create_user_denies_self_escalation_to_root(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_append = _patch_audit(monkeypatch)
    conn = AsyncMock()
    _override_caller(conn, _NONROOT_ADMIN_CALLER)

    resp = await client.post(
        "/api/v1/auth/users",
        json={"username": "wannabe", "email": "w@x.co", "is_root": True},
        headers={**TENANT_HEADERS, "X-User-Id": str(_NONROOT_ADMIN_CALLER.user_id)},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    mock_append.assert_not_awaited()
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_user_root_can_create_root(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_append = _patch_audit(monkeypatch)
    new_id = uuid.uuid4()
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_FakeTx())
    conn.fetchrow.return_value = {
        "id": new_id,
        "username": "SUPER",
        "email": "s@x.co",
        "keycloak_sub": None,
        "is_root": True,
        "is_active": True,
    }
    _override_caller(conn, _ADMIN_CALLER)  # is_root=True

    resp = await client.post(
        "/api/v1/auth/users",
        json={"username": "super", "email": "s@x.co", "is_root": True},
        headers={**TENANT_HEADERS, "X-User-Id": str(_ADMIN_CALLER.user_id)},
    )
    assert resp.status_code == 201
    assert resp.json()["is_root"] is True
    mock_append.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_group_permission_denies_granting_root(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_append = _patch_audit(monkeypatch)
    conn = AsyncMock()
    conn.fetchval.return_value = "USUA_PERM_ROOT"  # get_permission_name
    _override_caller(conn, _NONROOT_ADMIN_CALLER)
    group_id, perm_id = uuid.uuid4(), uuid.uuid4()

    resp = await client.put(
        f"/api/v1/auth/groups/{group_id}/permissions/{perm_id}",
        json={"crud": 3},
        headers={**TENANT_HEADERS, "X-User-Id": str(_NONROOT_ADMIN_CALLER.user_id)},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    mock_append.assert_not_awaited()
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_group_permission_non_root_can_revoke_root(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """crud=0 (revocar) nunca está bloqueado: solo la CONCESIÓN lo está."""
    mock_append = _patch_audit(monkeypatch)
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_FakeTx())
    conn.fetchval.return_value = "USUA_PERM_ROOT"
    _override_caller(conn, _NONROOT_ADMIN_CALLER)
    group_id, perm_id = uuid.uuid4(), uuid.uuid4()

    resp = await client.put(
        f"/api/v1/auth/groups/{group_id}/permissions/{perm_id}",
        json={"crud": 0},
        headers={**TENANT_HEADERS, "X-User-Id": str(_NONROOT_ADMIN_CALLER.user_id)},
    )
    assert resp.status_code == 200
    mock_append.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_group_permission_root_can_grant_root(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_append = _patch_audit(monkeypatch)
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_FakeTx())
    _override_caller(conn, _ADMIN_CALLER)  # is_root=True: no consulta get_permission_name
    group_id, perm_id = uuid.uuid4(), uuid.uuid4()

    resp = await client.put(
        f"/api/v1/auth/groups/{group_id}/permissions/{perm_id}",
        json={"crud": 5},
        headers={**TENANT_HEADERS, "X-User-Id": str(_ADMIN_CALLER.user_id)},
    )
    assert resp.status_code == 200
    mock_append.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_permissions_excludes_root_for_non_root_caller(client: AsyncClient) -> None:
    conn = AsyncMock()
    conn.fetch.return_value = [
        {"id": uuid.uuid4(), "nombre": "PERM_RADI", "descripcion": "base"},
        {"id": uuid.uuid4(), "nombre": "USUA_PERM_ROOT", "descripcion": "root"},
    ]
    _override_caller(conn, _NONROOT_ADMIN_CALLER)

    resp = await client.get(
        "/api/v1/auth/permissions",
        headers={**TENANT_HEADERS, "X-User-Id": str(_NONROOT_ADMIN_CALLER.user_id)},
    )
    assert resp.status_code == 200
    nombres = [p["nombre"] for p in resp.json()]
    assert "PERM_RADI" in nombres
    assert "USUA_PERM_ROOT" not in nombres


@pytest.mark.asyncio
async def test_list_permissions_includes_root_for_root_caller(client: AsyncClient) -> None:
    conn = AsyncMock()
    conn.fetch.return_value = [
        {"id": uuid.uuid4(), "nombre": "PERM_RADI", "descripcion": "base"},
        {"id": uuid.uuid4(), "nombre": "USUA_PERM_ROOT", "descripcion": "root"},
    ]
    _override_caller(conn, _ADMIN_CALLER)  # is_root=True

    resp = await client.get(
        "/api/v1/auth/permissions",
        headers={**TENANT_HEADERS, "X-User-Id": str(_ADMIN_CALLER.user_id)},
    )
    assert resp.status_code == 200
    nombres = [p["nombre"] for p in resp.json()]
    assert "USUA_PERM_ROOT" in nombres
