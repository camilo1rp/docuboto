# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de orpycamcp_common.audit (lógica de hash; sin BD real)."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest
from orpycamcp_common import audit

_FIELDS = dict(
    tenant_slug="icetex",
    service="document-service",
    actor="user-1",
    canal="api",
    action="radicado.creado",
    object_type="radicado",
    object_ref="2024-ICETEX-E-001234",
    payload={"asunto": "prueba"},
)


def test_compute_hash_is_deterministic() -> None:
    h1 = audit.compute_hash(None, **_FIELDS)
    h2 = audit.compute_hash(None, **_FIELDS)
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex


def test_compute_hash_chains_on_prev() -> None:
    h1 = audit.compute_hash(None, **_FIELDS)
    h2 = audit.compute_hash(h1, **_FIELDS)
    # Mismo contenido pero distinto prev_hash → distinto hash (encadenamiento).
    assert h1 != h2


def test_compute_hash_changes_with_payload() -> None:
    h1 = audit.compute_hash(None, **_FIELDS)
    altered = {**_FIELDS, "payload": {"asunto": "otro"}}
    h2 = audit.compute_hash(None, **altered)
    assert h1 != h2


class _FakeTx:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc: object) -> bool:
        return False


def _mock_conn(prev_hash: str | None = None) -> AsyncMock:
    conn = AsyncMock()
    conn.fetchval.return_value = prev_hash
    conn.execute.return_value = None
    conn.transaction = MagicMock(return_value=_FakeTx())
    return conn


@pytest.mark.asyncio
async def test_append_inserts_and_returns_hash() -> None:
    conn = _mock_conn(prev_hash=None)
    row_hash = await audit.append(
        conn,
        tenant_slug="icetex",
        service="document-service",
        action="radicado.creado",
        object_type="radicado",
        object_ref="2024-ICETEX-E-001234",
        actor="user-1",
        payload={"asunto": "prueba"},
    )
    assert len(row_hash) == 64
    # Se tomó el advisory lock + se hizo el INSERT.
    assert conn.execute.await_count == 2
    insert_call = conn.execute.await_args_list[-1]
    assert "INSERT INTO public.audit_log" in insert_call.args[0]
    # El hash insertado coincide con el devuelto.
    assert insert_call.args[-1] == row_hash
    assert insert_call.args[-2] is None  # prev_hash


@pytest.mark.asyncio
async def test_append_uses_system_tenant_when_none() -> None:
    conn = _mock_conn()
    await audit.append(
        conn,
        tenant_slug=None,
        service="auth-service",
        action="auth.logout",
        object_type="auth_session",
    )
    insert_call = conn.execute.await_args_list[-1]
    assert insert_call.args[1] == audit.SYSTEM_TENANT


# ---------------------------------------------------------------------------
# D-03: canal validation
# ---------------------------------------------------------------------------

def test_valid_canales_constant_contains_expected_values() -> None:
    assert audit.VALID_CANALES == {"api", "ui", "mcp", "system"}


@pytest.mark.parametrize("canal", ["api", "ui", "mcp", "system"])
def test_valid_canal_does_not_raise(canal: str) -> None:
    # _validate_canal is internal but testable via compute_hash (it calls append);
    # we call it through append to stay black-box.
    audit._validate_canal(canal)  # must not raise


def test_invalid_canal_raises_value_error() -> None:
    with pytest.raises(ValueError, match="Invalid canal"):
        audit._validate_canal("web")


def test_invalid_canal_empty_string_raises() -> None:
    with pytest.raises(ValueError, match="Invalid canal"):
        audit._validate_canal("")


@pytest.mark.asyncio
async def test_append_rejects_invalid_canal() -> None:
    conn = _mock_conn()
    with pytest.raises(ValueError, match="Invalid canal"):
        await audit.append(
            conn,
            tenant_slug="icetex",
            service="auth-service",
            action="auth.login_success",
            object_type="auth_session",
            canal="web",  # not in VALID_CANALES
        )
    # No DB call should have been made.
    conn.execute.assert_not_called()


@pytest.mark.asyncio
async def test_append_accepts_mcp_canal() -> None:
    conn = _mock_conn()
    row_hash = await audit.append(
        conn,
        tenant_slug="icetex",
        service="mcp-server",
        action="mcp.query",
        object_type="radicado",
        canal="mcp",
    )
    assert len(row_hash) == 64
    insert_call = conn.execute.await_args_list[-1]
    # canal is the 4th positional arg ($4) after tenant, service, actor
    assert insert_call.args[4] == "mcp"


# ---------------------------------------------------------------------------
# append_denial: motivo del asiento (RBAC denial audit)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_append_denial_defaults_motivo_permiso_insuficiente() -> None:
    conn = _mock_conn()
    await audit.append_denial(
        conn,
        tenant_slug="icetex",
        service="document-service",
        actor="user-1",
        perm_name="PERM_RADI",
        min_crud=2,
    )
    insert_call = conn.execute.await_args_list[-1]
    payload = json.loads(insert_call.args[8])
    assert payload["motivo"] == "permiso_insuficiente"
    assert payload["perm"] == "PERM_RADI"
    assert payload["min_crud"] == 2


@pytest.mark.asyncio
async def test_append_denial_accepts_custom_motivo() -> None:
    """La rama "no aprovisionado" de auth-service pasa un motivo distinto para
    no confundirse con una denegación por permiso insuficiente al filtrar
    audit_log por payload.motivo."""
    conn = _mock_conn()
    await audit.append_denial(
        conn,
        tenant_slug="icetex",
        service="auth-service",
        actor="user-1",
        perm_name="__not_provisioned__",
        min_crud=0,
        motivo="usuario_no_aprovisionado",
    )
    insert_call = conn.execute.await_args_list[-1]
    payload = json.loads(insert_call.args[8])
    assert payload["motivo"] == "usuario_no_aprovisionado"


@pytest.mark.asyncio
async def test_append_denial_is_best_effort_on_failure() -> None:
    conn = _mock_conn()
    conn.execute.side_effect = RuntimeError("db down")
    # Must not raise — best-effort, never turns a 403 into a 500.
    await audit.append_denial(
        conn,
        tenant_slug="icetex",
        service="document-service",
        actor="user-1",
        perm_name="PERM_RADI",
    )
