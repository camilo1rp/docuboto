# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Auditoría best-effort de accesos DENEGADOS por el gate RBAC (RF-SEG / MoReq).

Gemelo de `document-service/tests/test_rbac_denial_audit.py`. storage-service no
tenía gate RBAC alguno antes de E10 Increment A — `POST /proteger-indice` fue el
primer endpoint gateado; `POST /aip` (Increment B) usa el mismo
`require_permission("USUA_PERM_EXPEDIENTE", min_crud=3)`, así que el contrato de
auditoría de denegación (verificado aquí sobre `/proteger-indice`, que ejerce el
mismo `clearance.py` compartido) aplica igual a ambos:
  (a) un usuario IDENTIFICADO con tenant válido → SÍ se audita.
  (b) una sonda sin identidad (sin X-User-Id) → NO se audita (regla anti-ruido).
  (c) el 403 ocurre igual aunque la propia auditoría falle (best-effort real).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import TENANT_HEADERS

# `/proteger-indice` pasó a X-Internal-Token (AREA-08-16); `/eventos` (POST)
# conserva el gate RBAC `USUA_PERM_EXPEDIENTE` min_crud=3 que estos tests miden.
_RUTA = "/api/v1/preservacion/eventos"
_PAYLOAD = {"object_ref": "EXP-1", "tipo": "INGESTA"}


def _audit_insert_calls(mock_conn: AsyncMock) -> list:
    return [
        c
        for c in mock_conn.execute.call_args_list
        if c.args and "INSERT INTO public.audit_log" in c.args[0]
    ]


@pytest.mark.asyncio
async def test_denial_of_identified_user_is_audited(client_with_conn) -> None:
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = None  # check_permission → denegado

    response = await client.post(
        _RUTA, json=_PAYLOAD, headers=TENANT_HEADERS
    )

    assert response.status_code == 403
    inserts = _audit_insert_calls(mock_conn)
    assert len(inserts) == 1
    args = inserts[0].args
    # _INSERT_SQL params: (sql, tenant, service, actor, canal, action, object_type,
    #                       object_ref, payload_json, prev_hash, hash)
    assert args[1] == TENANT_HEADERS["X-Tenant-Slug"]
    assert args[2] == "storage-service"
    assert args[3] == TENANT_HEADERS["X-User-Id"]
    assert args[5] == "acceso.denegado"
    assert args[6] == "rbac"
    assert args[7] == "USUA_PERM_EXPEDIENTE"


@pytest.mark.asyncio
async def test_denial_without_identity_is_not_audited(client_with_conn) -> None:
    """Sonda anónima (sin X-User-Id): sigue siendo 403, pero NO deja traza."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = True  # aunque "hubiera" permiso, sin identidad no aplica

    response = await client.post(
        _RUTA,
        json=_PAYLOAD,
        headers={"X-Tenant-Slug": TENANT_HEADERS["X-Tenant-Slug"]},
    )

    assert response.status_code == 403
    assert _audit_insert_calls(mock_conn) == []


@pytest.mark.asyncio
async def test_denial_survives_audit_failure(client_with_conn) -> None:
    """Best-effort real: si append_denial explota, la respuesta sigue siendo 403,
    nunca 500."""
    client, mock_conn, _ = client_with_conn
    mock_conn.fetchval.return_value = None  # check_permission → denegado

    with patch(
        "app.core.clearance.audit.append_denial",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        response = await client.post(
            _RUTA, json=_PAYLOAD, headers=TENANT_HEADERS
        )

    assert response.status_code == 403
    assert response.json()["detail"]["error"] == "forbidden"
