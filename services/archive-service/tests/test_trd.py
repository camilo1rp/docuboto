# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del modelo TRD/CCD: subseries y cálculo de retención (E04, ADR-015)."""

import json
import uuid

import pytest

from tests.conftest import TENANT_HEADERS, make_trd_serie_record


@pytest.mark.asyncio
async def test_create_subserie_with_two_phase_retention(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # USUA_PERM_TRD granted (gate)
    parent = uuid.uuid4()
    mock_conn.fetchrow.return_value = make_trd_serie_record(
        code="100.1",
        name="Contratos de obra",
        disposition="CT",
        parent_id=parent,
        archivo_gestion_years=3,
        archivo_central_years=7,
    )

    resp = await client.post(
        "/api/v1/trd",
        json={
            "code": "100.1",
            "name": "Contratos de obra",
            "disposition": "CT",
            "parent_id": str(parent),
            "archivo_gestion_years": 3,
            "archivo_central_years": 7,
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["parent_id"] == str(parent)
    assert body["archivo_gestion_years"] == 3
    assert body["disposition"] == "CT"


@pytest.mark.asyncio
async def test_compute_retention_schedule(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    # serie: 3 años gestión + 7 central, disposición conserve→CT.
    mock_conn.fetchrow.return_value = make_trd_serie_record(
        code="100",
        disposition="conserve",
        archivo_gestion_years=3,
        archivo_central_years=7,
    )
    serie_id = make_trd_serie_record()["id"]

    resp = await client.get(
        f"/api/v1/trd/{serie_id}/retention?closed_at=2024-01-15", headers=TENANT_HEADERS
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["fin_archivo_gestion"] == "2027-01-15"  # +3
    assert body["fin_archivo_central"] == "2034-01-15"  # +3+7
    assert body["disposition_code"] == "CT"
    assert body["disposition_label"] == "Conservación Total"


@pytest.mark.asyncio
async def test_compute_retention_serie_not_found(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = None

    resp = await client.get(
        f"/api/v1/trd/{uuid.uuid4()}/retention?closed_at=2024-01-15", headers=TENANT_HEADERS
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# RBAC gate: USUA_PERM_TRD en las mutaciones de trd.py (create/update series).
# Las lecturas (GET /trd, /{id}, /{id}/retention) las consume el dropdown de
# radicación y quedan abiertas — sin cambio, solo _require_tenant.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_trd_serie_forbidden_without_permission(client_with_conn) -> None:
    """Mutación representativa: sin USUA_PERM_TRD, `require_permission` corta
    con 403 antes de tocar la lógica de negocio (no se inserta la serie)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False  # permiso denegado

    resp = await client.post(
        "/api/v1/trd",
        json={"code": "100.1", "name": "Contratos de obra", "disposition": "CT"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_create_trd_serie_forbidden_without_user_id(client_with_conn) -> None:
    """Fail-closed sobre la IDENTIDAD: sin X-User-Id no hay usuario que
    resolver → 403, aunque la BD "tuviera" el permiso."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # aunque "hubiera" permiso, sin identidad no aplica

    resp = await client.post(
        "/api/v1/trd",
        json={"code": "100.1", "name": "Contratos de obra", "disposition": "CT"},
        headers={"X-Tenant-Slug": TENANT_HEADERS["X-Tenant-Slug"]},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_update_trd_serie_forbidden_without_permission(client_with_conn) -> None:
    """update_trd_serie (PATCH) también está gateado: sin USUA_PERM_TRD,
    `require_permission` corta con 403 antes de tocar la lógica de negocio."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False  # permiso denegado

    resp = await client.patch(
        f"/api/v1/trd/{uuid.uuid4()}",
        json={"name": "Contratos renombrados"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_update_trd_serie_forbidden_without_user_id(client_with_conn) -> None:
    """Fail-closed sobre la IDENTIDAD: sin X-User-Id no hay usuario que
    resolver → 403, aunque la BD "tuviera" el permiso."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # aunque "hubiera" permiso, sin identidad no aplica

    resp = await client.patch(
        f"/api/v1/trd/{uuid.uuid4()}",
        json={"name": "Contratos renombrados"},
        headers={"X-Tenant-Slug": TENANT_HEADERS["X-Tenant-Slug"]},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_create_trd_serie_writes_audit_entry(client_with_conn) -> None:
    """Media (RBAC follow-up): crear una serie TRD deja traza en `audit_log`
    con el actor (X-User-Id) y el código de la serie, igual que el alta de un
    expediente (Ley 594/2000 art. 24 — la TRD gobierna retención/disposición)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # USUA_PERM_TRD granted (gate)
    mock_conn.fetchrow.return_value = make_trd_serie_record(code="100.1")

    resp = await client.post(
        "/api/v1/trd",
        json={"code": "100.1", "name": "Contratos de obra", "disposition": "CT"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201

    audit_calls = [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]
    # args: (sql, tenant, service, actor, canal, action, object_type, object_ref, payload, prev_hash, hash)
    assert len(audit_calls) == 1
    assert audit_calls[0][3] == TENANT_HEADERS["X-User-Id"]
    assert audit_calls[0][5] == "archive.trd_serie_created"
    assert audit_calls[0][6] == "trd_serie"
    assert audit_calls[0][7] == "100.1"


@pytest.mark.asyncio
async def test_update_trd_serie_writes_audit_entry(client_with_conn) -> None:
    """Media (RBAC follow-up): actualizar una serie TRD deja traza en
    `audit_log`, incluyendo los campos modificados en el payload."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # USUA_PERM_TRD granted (gate)
    serie_id = uuid.uuid4()
    existing = make_trd_serie_record(serie_id=serie_id, code="100")
    updated = make_trd_serie_record(serie_id=serie_id, code="100", name="Contratos renombrados")
    mock_conn.fetchrow.side_effect = [existing, updated]

    resp = await client.patch(
        f"/api/v1/trd/{serie_id}",
        json={"name": "Contratos renombrados"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 200

    audit_calls = [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(audit_calls) == 1
    assert audit_calls[0][3] == TENANT_HEADERS["X-User-Id"]
    assert audit_calls[0][5] == "archive.trd_serie_updated"
    assert audit_calls[0][6] == "trd_serie"
    assert audit_calls[0][7] == "100"
    assert json.loads(audit_calls[0][8])["changes"] == {"name": "Contratos renombrados"}


@pytest.mark.asyncio
async def test_list_trd_series_open_read_no_user_id_needed(client_with_conn) -> None:
    """Confirma que la lectura sigue ABIERTA (solo `_require_tenant`): sin
    X-User-Id ni permiso mockeado, GET /trd sigue respondiendo 200."""
    client, mock_conn = client_with_conn
    mock_conn.fetch.return_value = []
    mock_conn.fetchval.return_value = 0

    resp = await client.get(
        "/api/v1/trd", headers={"X-Tenant-Slug": TENANT_HEADERS["X-Tenant-Slug"]}
    )

    assert resp.status_code == 200
