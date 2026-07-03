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
  (b) una petición SIN identidad (sin X-User-Id → 401 vía ``get_actor``, que se
      resuelve ANTES de ``check_permission``) → NO se audita (regla anti-ruido).
  (c) el 403 ocurre igual aunque la propia auditoría falle (best-effort real).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from tests.conftest import TENANT_HEADERS, USER


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
    mock_conn.fetchval.return_value = None  # check_permission → denegado

    response = await client.post(
        "/api/v1/signature/sign-xml",
        json={"payload_xml": "<x/>", "objeto_tipo": "indice"},
        headers=TENANT_HEADERS,
    )

    assert response.status_code == 403
    inserts = _audit_insert_calls(mock_conn)
    assert len(inserts) == 1
    args = inserts[0].args
    # _INSERT_SQL params: (sql, tenant, service, actor, canal, action, object_type,
    #                       object_ref, payload_json, prev_hash, hash)
    assert args[1] == TENANT_HEADERS["X-Tenant-Slug"]
    assert args[2] == "signature-service"
    assert args[3] == str(USER)
    assert args[5] == "acceso.denegado"
    assert args[6] == "rbac"
    assert args[7] == "PERM_FIRMA"


@pytest.mark.asyncio
async def test_denial_without_identity_is_not_audited(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Sin X-User-Id, ``get_actor`` devuelve 401 antes de llegar a
    ``check_permission`` — no hay caso anónimo que audite el gate de permiso
    (no es una sonda que la auditoría deba registrar)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # aunque "hubiera" permiso, sin identidad no aplica

    response = await client.post(
        "/api/v1/signature/sign-xml",
        json={"payload_xml": "<x/>", "objeto_tipo": "indice"},
        headers={"X-Tenant-Slug": TENANT_HEADERS["X-Tenant-Slug"]},
    )

    assert response.status_code == 401
    assert _audit_insert_calls(mock_conn) == []


@pytest.mark.asyncio
async def test_denial_survives_audit_failure(
    client_with_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Best-effort real: si append_denial explota, la respuesta sigue siendo 403,
    nunca 500."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = None  # check_permission → denegado

    with patch(
        "app.core.clearance.audit.append_denial",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        response = await client.post(
            "/api/v1/signature/sign-xml",
            json={"payload_xml": "<x/>", "objeto_tipo": "indice"},
            headers=TENANT_HEADERS,
        )

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "forbidden"
