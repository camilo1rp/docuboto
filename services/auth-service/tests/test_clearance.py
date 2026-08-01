# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de clasificación de seguridad y clearance (E08, RF-SEG-08)."""

import uuid
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest
from httpx import AsyncClient

from app.core.authz import Caller, get_caller
from app.core.database import get_tenant_conn
from app.main import app
from app.services import clearance_service as clearance_service_module

TENANT_HEADERS = {"X-Tenant-Slug": "demo"}
_USER = uuid.uuid4()


class _FakeTx:
    """Context manager async no-op para simular conn.transaction()."""

    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _caller(*, is_root: bool = False, admin: bool = False) -> Caller:
    perms = {"USUA_PERM_ADMIN": 5} if admin else {"PERM_RADI": 3}
    return Caller(user_id=_USER, keycloak_sub="kc", is_root=is_root, permissions=perms)


def _override(conn: AsyncMock, caller: Caller) -> None:
    async def _dep():
        yield conn

    # orpycamcp_common.audit.append() envuelve el INSERT en `async with
    # conn.transaction()` — PUT .../clearance ahora audita, así que el mock
    # necesita soportar el protocolo de contexto asíncrono por defecto.
    conn.transaction = MagicMock(return_value=_FakeTx())
    app.dependency_overrides[get_tenant_conn] = _dep
    app.dependency_overrides[get_caller] = lambda: caller


@pytest.mark.asyncio
async def test_list_security_levels(client: AsyncClient) -> None:
    conn = AsyncMock()
    conn.fetch.return_value = [
        {"code": 1, "nombre": "PUBLICA"},
        {"code": 2, "nombre": "RESERVADA"},
        {"code": 3, "nombre": "CLASIFICADA"},
    ]
    _override(conn, _caller())

    resp = await client.get("/api/v1/auth/security-levels", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert [lv["nombre"] for lv in resp.json()] == ["PUBLICA", "RESERVADA", "CLASIFICADA"]


@pytest.mark.asyncio
async def test_clearance_check_allowed(client: AsyncClient) -> None:
    conn = AsyncMock()
    conn.fetchval.return_value = 2  # caller_max_level = RESERVADA
    _override(conn, _caller())

    resp = await client.get("/api/v1/auth/clearance/check?level=2", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["allowed"] is True and body["max_level"] == 2


@pytest.mark.asyncio
async def test_clearance_check_denied(client: AsyncClient) -> None:
    conn = AsyncMock()
    conn.fetchval.return_value = 1  # solo PUBLICA
    _override(conn, _caller())

    resp = await client.get("/api/v1/auth/clearance/check?level=3", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["allowed"] is False


@pytest.mark.asyncio
async def test_clearance_root_accesses_all(client: AsyncClient) -> None:
    conn = AsyncMock()
    _override(conn, _caller(is_root=True))

    resp = await client.get("/api/v1/auth/clearance", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["max_level"] == 3
    assert body["accessible_levels"] == [1, 2, 3]


@pytest.mark.asyncio
async def test_set_group_clearance(client: AsyncClient) -> None:
    conn = AsyncMock()
    # level_exists → existe; caller_max_level (no-write-up) → 2 (>= max_level pedido);
    # get_group_clearance (previous, dentro del service) → 1; el 4to valor
    # alimenta el lookup de prev_hash de `_audit.append` (real, sin mock aquí).
    conn.fetchval.side_effect = [1, 2, 1, None]
    conn.execute.return_value = None
    _override(conn, _caller(admin=True))

    resp = await client.put(
        f"/api/v1/auth/groups/{uuid.uuid4()}/clearance",
        json={"max_level": 2},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_get_group_clearance(client: AsyncClient) -> None:
    group_id = uuid.uuid4()
    conn = AsyncMock()
    # group_exists → fetchval(1); get_group_clearance → fetchval(max_level).
    conn.fetchval.side_effect = [1, 2]
    _override(conn, _caller(admin=True))

    resp = await client.get(f"/api/v1/auth/groups/{group_id}/clearance", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    body = resp.json()
    assert body == {"group_id": str(group_id), "max_level": 2}


@pytest.mark.asyncio
async def test_get_group_clearance_unset_defaults_to_none(client: AsyncClient) -> None:
    """Grupo sin fila en role_clearance: max_level=None (implícito PUBLICA=1)."""
    group_id = uuid.uuid4()
    conn = AsyncMock()
    conn.fetchval.side_effect = [1, None]
    _override(conn, _caller(admin=True))

    resp = await client.get(f"/api/v1/auth/groups/{group_id}/clearance", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["max_level"] is None


@pytest.mark.asyncio
async def test_get_group_clearance_group_not_found(client: AsyncClient) -> None:
    conn = AsyncMock()
    conn.fetchval.return_value = None  # group_exists → False
    _override(conn, _caller(admin=True))

    resp = await client.get(
        f"/api/v1/auth/groups/{uuid.uuid4()}/clearance", headers=TENANT_HEADERS
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_group_clearance_other_tenant_group_is_404(client: AsyncClient) -> None:
    """Un group_id de OTRO tenant no existe en este search_path: group_exists() es
    False igual que un id inexistente — 404 indistinguible, nunca 403."""
    conn = AsyncMock()
    conn.fetchval.return_value = None
    _override(conn, _caller(admin=True))

    resp = await client.get(
        f"/api/v1/auth/groups/{uuid.uuid4()}/clearance", headers=TENANT_HEADERS
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_group_clearance_denied_without_admin(client: AsyncClient) -> None:
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_FakeTx())  # append_denial() best-effort
    _override(conn, _caller(admin=False))

    resp = await client.get(
        f"/api/v1/auth/groups/{uuid.uuid4()}/clearance", headers=TENANT_HEADERS
    )
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_set_group_clearance_denied_without_admin(client: AsyncClient) -> None:
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=_FakeTx())  # append_denial() best-effort
    _override(conn, _caller(admin=False))

    resp = await client.put(
        f"/api/v1/auth/groups/{uuid.uuid4()}/clearance",
        json={"max_level": 2},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 403


# --- no-write-up del clearance de grupo (cierre del hallazgo Alto) ----------
@pytest.mark.asyncio
async def test_set_group_clearance_no_write_up_denied(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un admin no-ROOT con clearance efectivo 2 no puede fijar max_level=3 en
    un grupo: 403, y la mutación nunca se intenta (sin asiento de auditoría)."""
    mock_append = _patch_audit(monkeypatch)
    conn = AsyncMock()
    # level_exists → existe; caller_max_level (no-write-up) → 2 (< 3 pedido).
    conn.fetchval.side_effect = [1, 2]
    _override(conn, _caller(admin=True))

    resp = await client.put(
        f"/api/v1/auth/groups/{uuid.uuid4()}/clearance",
        json={"max_level": 3},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    mock_append.assert_not_awaited()
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_group_clearance_no_write_up_allows_equal_level(client: AsyncClient) -> None:
    """El mismo admin SÍ puede fijar max_level=2 (igual a su propio clearance)."""
    conn = AsyncMock()
    # level_exists, caller_max_level, previous, prev_hash (audit real, sin mock).
    conn.fetchval.side_effect = [1, 2, 1, None]
    conn.execute.return_value = None
    _override(conn, _caller(admin=True))

    resp = await client.put(
        f"/api/v1/auth/groups/{uuid.uuid4()}/clearance",
        json={"max_level": 2},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_set_group_clearance_root_bypasses_no_write_up(client: AsyncClient) -> None:
    """ROOT puede fijar cualquier nivel, sin resolver su propio clearance efectivo."""
    conn = AsyncMock()
    # level_exists, previous (SIN caller_max_level), prev_hash (audit real, sin mock).
    conn.fetchval.side_effect = [1, 1, None]
    conn.execute.return_value = None
    _override(conn, _caller(is_root=True, admin=True))

    resp = await client.put(
        f"/api/v1/auth/groups/{uuid.uuid4()}/clearance",
        json={"max_level": 3},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 204


# --- Canal A: auditoría inmutable de la subida/bajada de clearance ---------
def _patch_audit(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    mock_append = AsyncMock(return_value="fake-hash")
    monkeypatch.setattr(clearance_service_module, "_audit", MagicMock(append=mock_append))
    monkeypatch.setattr(clearance_service_module, "_AUDIT_AVAILABLE", True)
    return mock_append


@pytest.mark.asyncio
async def test_set_group_clearance_audits_previous_and_new_level(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El payload debe permitir reconstruir el cambio: nivel anterior Y nuevo
    (sin el previo no se distingue una subida de una bajada)."""
    mock_append = _patch_audit(monkeypatch)
    group_id = uuid.uuid4()
    conn = AsyncMock()
    # level_exists → 1; caller_max_level (no-write-up) → 2; get_group_clearance
    # (previo, dentro del service) → 1.
    conn.fetchval.side_effect = [1, 2, 1]
    conn.execute.return_value = None
    _override(conn, _caller(admin=True))

    resp = await client.put(
        f"/api/v1/auth/groups/{group_id}/clearance",
        json={"max_level": 2},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 204
    mock_append.assert_awaited_once()
    kwargs = mock_append.call_args.kwargs
    assert kwargs["action"] == "clearance.grupo_actualizado"
    assert kwargs["object_type"] == "clearance_grupo"
    assert kwargs["object_ref"] == str(group_id)
    assert kwargs["payload"] == {
        "group_id": str(group_id),
        "previous_max_level": 1,
        "new_max_level": 2,
    }


@pytest.mark.asyncio
async def test_set_group_clearance_failure_leaves_no_audit_entry(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Atomicidad (mitad 1): grupo inexistente (FK) → sin asiento."""
    mock_append = _patch_audit(monkeypatch)
    conn = AsyncMock()
    # level_exists OK; caller_max_level (no-write-up) OK; get_group_clearance sin fila.
    conn.fetchval.side_effect = [1, 2, None]
    conn.execute.side_effect = asyncpg.ForeignKeyViolationError("fk")
    _override(conn, _caller(admin=True))

    resp = await client.put(
        f"/api/v1/auth/groups/{uuid.uuid4()}/clearance",
        json={"max_level": 2},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 404
    mock_append.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_group_clearance_audit_failure_reverts_mutation(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Atomicidad (mitad 2): si `_audit.append` falla, la subida de clearance
    no se confirma como éxito (500, no 204)."""
    mock_append = AsyncMock(side_effect=RuntimeError("audit backend down"))
    monkeypatch.setattr(clearance_service_module, "_audit", MagicMock(append=mock_append))
    monkeypatch.setattr(clearance_service_module, "_AUDIT_AVAILABLE", True)
    conn = AsyncMock()
    conn.fetchval.side_effect = [1, 2, 1]
    conn.execute.return_value = None
    _override(conn, _caller(admin=True))

    with pytest.raises(RuntimeError, match="audit backend down"):
        await client.put(
            f"/api/v1/auth/groups/{uuid.uuid4()}/clearance",
            json={"max_level": 2},
            headers=TENANT_HEADERS,
        )
    mock_append.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_group_clearance_500_when_audit_lib_unavailable(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(clearance_service_module, "_AUDIT_AVAILABLE", False)
    conn = AsyncMock()
    # 3 satisface tanto level_exists (no-None) como el no-write-up (>= max_level=2)
    # para llegar al guard de auditoría no disponible.
    conn.fetchval.return_value = 3
    _override(conn, _caller(admin=True))

    resp = await client.put(
        f"/api/v1/auth/groups/{uuid.uuid4()}/clearance",
        json={"max_level": 2},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 500
    assert resp.json()["detail"]["error"] == "auditoria_no_disponible"
