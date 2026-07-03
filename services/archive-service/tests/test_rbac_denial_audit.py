# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Auditoría best-effort de accesos DENEGADOS por el gate RBAC (RF-SEG / MoReq).

``require_permission`` levanta el 403 antes de que la lógica de negocio (y su
propia llamada a ``audit.append``) se ejecute — sin este seguimiento, ninguna
denegación de permiso queda registrada en ``audit_log``. Cubre:
  (a) un usuario IDENTIFICADO con tenant válido → SÍ se audita.
  (b) una sonda sin identidad (sin X-User-Id) → NO se audita (regla anti-ruido).
  (c) el 403 ocurre igual aunque la propia auditoría falle (best-effort real).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from tests.conftest import TENANT_HEADERS, USER_ID

_CREATE_EXP_PAYLOAD = {
    "name": "Contrato 2024-001",
    "description": "Contrato de prestación de servicios",
}


def _audit_insert_calls(mock_conn: AsyncMock) -> list:
    return [
        c
        for c in mock_conn.execute.call_args_list
        if c.args and "INSERT INTO public.audit_log" in c.args[0]
    ]


@pytest.mark.asyncio
async def test_denial_of_identified_user_is_audited(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False  # D-08: permiso denegado

    response = await client.post(
        "/api/v1/expedientes",
        json=_CREATE_EXP_PAYLOAD,
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 403
    inserts = _audit_insert_calls(mock_conn)
    assert len(inserts) == 1
    args = inserts[0].args
    # _INSERT_SQL params: (sql, tenant, service, actor, canal, action, object_type,
    #                       object_ref, payload_json, prev_hash, hash)
    assert args[1] == TENANT_HEADERS["X-Tenant-Slug"]
    assert args[2] == "archive-service"
    assert args[3] == str(USER_ID)
    assert args[5] == "acceso.denegado"
    assert args[6] == "rbac"
    assert args[7] == "USUA_PERM_EXPEDIENTE"


@pytest.mark.asyncio
async def test_denial_without_identity_is_not_audited(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Sonda anónima (sin X-User-Id): sigue siendo 403, pero NO deja traza — evita
    que el rate-limiter y el escaneo no autenticado contaminen la cadena de hash."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # aunque "hubiera" permiso, sin identidad no aplica

    response = await client.post(
        "/api/v1/expedientes",
        json=_CREATE_EXP_PAYLOAD,
        headers={"X-Tenant-Slug": TENANT_HEADERS["X-Tenant-Slug"]},
    )

    assert response.status_code == 403
    assert _audit_insert_calls(mock_conn) == []


@pytest.mark.asyncio
async def test_denial_survives_audit_failure(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Best-effort real: si append_denial explota, la respuesta sigue siendo 403,
    nunca 500."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False  # D-08: permiso denegado

    with patch(
        "app.core.clearance.audit.append_denial",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        response = await client.post(
            "/api/v1/expedientes",
            json=_CREATE_EXP_PAYLOAD,
            headers=TENANT_HEADERS,
        )

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "forbidden"
