# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Auditoría best-effort de accesos DENEGADOS por el gate RBAC (RF-SEG / MoReq).

``require_permission`` (``app/core/authz.py``) levanta el 403 antes de que la
lógica de negocio (y su propia llamada a ``audit.append``) se ejecute — sin este
seguimiento, ninguna denegación de permiso quedaba registrada en ``audit_log``.
Cubre:
  (a) un llamante IDENTIFICADO (aprovisionado) sin el permiso → SÍ se audita.
  (b) una petición SIN identidad (sin X-User-Id → 401 vía ``get_caller``, que se
      resuelve ANTES del chequeo de permiso) → NO se audita (regla anti-ruido).
  (c) el 403 ocurre igual aunque la propia auditoría falle (best-effort real).
  (d) la rama "no aprovisionado" de ``get_caller`` (UUID válido, sin fila en
      ``auth_users``) también deja traza best-effort — el llamante SÍ mandó una
      identidad, no es una sonda anónima.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import AsyncClient

from app.core.authz import Caller, get_caller
from app.core.database import get_tenant_conn
from app.main import app

TENANT_HEADERS = {"X-Tenant-Slug": "demo"}
USER = uuid.uuid4()

_NON_ADMIN = Caller(
    user_id=USER,
    keycloak_sub="kc-op",
    is_root=False,
    permissions={"PERM_RADI": 3},
)


def _conn() -> AsyncMock:
    conn = AsyncMock()
    conn.transaction = MagicMock(return_value=AsyncMock())
    return conn


def _audit_insert_calls(mock_conn: AsyncMock) -> list:
    return [
        c
        for c in mock_conn.execute.call_args_list
        if c.args and "INSERT INTO public.audit_log" in c.args[0]
    ]


def _override_caller(conn: AsyncMock, caller: Caller) -> None:
    async def _dep() -> AsyncGenerator[AsyncMock, None]:
        yield conn

    app.dependency_overrides[get_tenant_conn] = _dep
    app.dependency_overrides[get_caller] = lambda: caller


@pytest.mark.asyncio
async def test_denial_of_identified_user_is_audited(client: AsyncClient) -> None:
    """USUA_PERM_ADMIN denegado a un caller identificado y sin ese permiso."""
    conn = _conn()
    _override_caller(conn, _NON_ADMIN)

    response = await client.post(
        "/api/v1/auth/users",
        json={"username": "x", "email": "x@x.co"},
        headers={**TENANT_HEADERS, "X-User-Id": "kc-op"},
    )

    assert response.status_code == 403
    inserts = _audit_insert_calls(conn)
    assert len(inserts) == 1
    args = inserts[0].args
    # _INSERT_SQL params: (sql, tenant, service, actor, canal, action, object_type,
    #                       object_ref, payload_json, prev_hash, hash)
    assert args[1] == TENANT_HEADERS["X-Tenant-Slug"]
    assert args[2] == "auth-service"
    assert args[3] == str(USER)
    assert args[5] == "acceso.denegado"
    assert args[6] == "rbac"
    assert args[7] == "USUA_PERM_ADMIN"
    # payload.motivo debe distinguir "permiso insuficiente" (caso normal) del
    # caso "usuario no aprovisionado" — ver test_not_provisioned_* más abajo.
    assert json.loads(args[8])["motivo"] == "permiso_insuficiente"


@pytest.mark.asyncio
async def test_denial_without_identity_is_not_audited(client: AsyncClient) -> None:
    """Sin X-User-Id, ``get_caller`` devuelve 401 antes del chequeo de permiso —
    no hay caso anónimo que audite el gate (no es una sonda que registrar)."""
    conn = _conn()

    async def _dep() -> AsyncGenerator[AsyncMock, None]:
        yield conn

    app.dependency_overrides[get_tenant_conn] = _dep  # get_caller real, sin override
    response = await client.post(
        "/api/v1/auth/users",
        json={"username": "x", "email": "x@x.co"},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 401
    assert _audit_insert_calls(conn) == []


@pytest.mark.asyncio
async def test_denial_survives_audit_failure(client: AsyncClient) -> None:
    """Best-effort real: si append_denial explota, la respuesta sigue siendo 403,
    nunca 500."""
    conn = _conn()
    _override_caller(conn, _NON_ADMIN)

    with patch(
        "app.core.authz.audit.append_denial",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        response = await client.post(
            "/api/v1/auth/users",
            json={"username": "x", "email": "x@x.co"},
            headers={**TENANT_HEADERS, "X-User-Id": "kc-op"},
        )

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_not_provisioned_identified_caller_is_audited(client: AsyncClient) -> None:
    """UUID válido en X-User-Id pero sin fila en ``auth_users`` (rama "no
    aprovisionado" de ``get_caller``, ~L93): el llamante SÍ se identificó, así
    que también deja traza best-effort (no es una sonda anónima)."""
    conn = _conn()
    conn.fetchrow.return_value = None  # get_user(UUID) → no row

    async def _dep() -> AsyncGenerator[AsyncMock, None]:
        yield conn

    app.dependency_overrides[get_tenant_conn] = _dep
    ghost_uuid = str(uuid.uuid4())  # valid UUID, not in auth_users
    response = await client.get(
        "/api/v1/auth/groups", headers={**TENANT_HEADERS, "X-User-Id": ghost_uuid}
    )

    assert response.status_code == 403
    inserts = _audit_insert_calls(conn)
    assert len(inserts) == 1
    args = inserts[0].args
    assert args[3] == ghost_uuid
    assert args[5] == "acceso.denegado"
    assert args[7] == "__not_provisioned__"
    # payload.motivo distingue este caso de una denegación por permiso
    # insuficiente — un analista que filtre audit_log por motivo no debe
    # confundir "usuario no aprovisionado" con "permiso_insuficiente".
    assert json.loads(args[8])["motivo"] == "usuario_no_aprovisionado"


# --- B6: el gate any-of pasa por `get_caller` (paridad con `require_permission`) ---
_PICKABLE = "/api/v1/auth/users/pickable"


@pytest.mark.asyncio
async def test_anyof_sin_identidad_es_401_y_no_se_audita(client: AsyncClient) -> None:
    conn = _conn()

    async def _dep() -> AsyncGenerator[AsyncMock, None]:
        yield conn

    app.dependency_overrides[get_tenant_conn] = _dep  # get_caller real
    response = await client.get(_PICKABLE, headers=TENANT_HEADERS)
    assert response.status_code == 401
    assert response.json()["detail"]["error"] == "unauthorized"
    assert _audit_insert_calls(conn) == []


@pytest.mark.asyncio
async def test_anyof_usuario_no_aprovisionado_deja_asiento(client: AsyncClient) -> None:
    conn = _conn()
    conn.fetchrow.return_value = None  # get_user -> sin fila

    async def _dep() -> AsyncGenerator[AsyncMock, None]:
        yield conn

    app.dependency_overrides[get_tenant_conn] = _dep
    fantasma = str(uuid.uuid4())
    response = await client.get(_PICKABLE, headers={**TENANT_HEADERS, "X-User-Id": fantasma})
    assert response.status_code == 403
    inserts = _audit_insert_calls(conn)
    assert len(inserts) == 1
    assert inserts[0].args[7] == "__not_provisioned__"
    assert json.loads(inserts[0].args[8])["motivo"] == "usuario_no_aprovisionado"


@pytest.mark.asyncio
async def test_anyof_denegado_asienta_la_etiqueta_a_o_b(client: AsyncClient) -> None:
    conn = _conn()
    conn.fetchval.return_value = False  # el any-of resuelve en la BD
    _override_caller(conn, _NON_ADMIN)
    response = await client.get(
        _PICKABLE, headers={**TENANT_HEADERS, "X-User-Id": str(uuid.uuid4())}
    )
    assert response.status_code == 403
    inserts = _audit_insert_calls(conn)
    assert len(inserts) == 1
    assert inserts[0].args[7] == "PERM_RADI|USUA_PERM_ADMIN"
