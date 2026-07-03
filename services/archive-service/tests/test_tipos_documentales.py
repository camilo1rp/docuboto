# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del catálogo de tipos documentales (E03, RF-MET-07)."""

import json
import uuid
from datetime import UTC, datetime

import pytest

from tests.conftest import TENANT_HEADERS


def _tipo_row(tipo_id: uuid.UUID, **kw) -> dict:
    base = {
        "id": tipo_id,
        "trd_serie_id": None,
        "code": "RES",
        "nombre": "Resolución",
        "obligatorio": True,
        "metadata_template": "Resolución",
        "activo": True,
        "created_at": datetime.now(tz=UTC),
    }
    base.update(kw)
    return base


@pytest.mark.asyncio
async def test_create_tipo(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # USUA_PERM_TRD granted (gate)
    tid = uuid.uuid4()
    mock_conn.fetchrow.return_value = _tipo_row(tid)

    resp = await client.post(
        "/api/v1/tipos-documentales",
        json={
            "code": "RES",
            "nombre": "Resolución",
            "obligatorio": True,
            "metadata_template": "Resolución",
        },
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["code"] == "RES"
    assert body["obligatorio"] is True


@pytest.mark.asyncio
async def test_list_tipos(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetch.return_value = [_tipo_row(uuid.uuid4()), _tipo_row(uuid.uuid4(), code="ACT")]
    resp = await client.get("/api/v1/tipos-documentales?activo=true", headers=TENANT_HEADERS)
    assert resp.status_code == 200
    assert len(resp.json()) == 2


@pytest.mark.asyncio
async def test_get_tipo_not_found(client_with_conn) -> None:
    client, mock_conn = client_with_conn
    mock_conn.fetchrow.return_value = None
    resp = await client.get(f"/api/v1/tipos-documentales/{uuid.uuid4()}", headers=TENANT_HEADERS)
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_tipo_missing_tenant(client_with_conn) -> None:
    """Sin X-Tenant-Slug, el gate RBAC (`require_permission`, dependency del
    decorador) corta la petición con 403 antes de que `_require_tenant`
    (Depends del handler) pueda emitir su propio 400 — `require_permission`
    exige el tenant para poder resolver el permiso y responde fail-closed."""
    client, _ = client_with_conn
    resp = await client.post(
        "/api/v1/tipos-documentales",
        json={"code": "RES", "nombre": "Resolución"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


# ---------------------------------------------------------------------------
# RBAC gate: USUA_PERM_TRD en las mutaciones de tipos_documentales.py
# (create/update/delete). Las lecturas (GET, list) quedan abiertas — sin
# cambio, solo _require_tenant.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_tipo_forbidden_without_permission(client_with_conn) -> None:
    """Mutación representativa: sin USUA_PERM_TRD, `require_permission` corta
    con 403 antes de tocar la lógica de negocio (no se inserta el tipo)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False  # permiso denegado

    resp = await client.post(
        "/api/v1/tipos-documentales",
        json={"code": "RES", "nombre": "Resolución"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_create_tipo_forbidden_without_user_id(client_with_conn) -> None:
    """Fail-closed sobre la IDENTIDAD: sin X-User-Id no hay usuario que
    resolver → 403, aunque la BD "tuviera" el permiso."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True

    resp = await client.post(
        "/api/v1/tipos-documentales",
        json={"code": "RES", "nombre": "Resolución"},
        headers={"X-Tenant-Slug": TENANT_HEADERS["X-Tenant-Slug"]},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_update_tipo_forbidden_without_permission(client_with_conn) -> None:
    """update_tipo (PATCH) también está gateado: sin USUA_PERM_TRD,
    `require_permission` corta con 403 antes de tocar la lógica de negocio."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False  # permiso denegado

    resp = await client.patch(
        f"/api/v1/tipos-documentales/{uuid.uuid4()}",
        json={"nombre": "Resolución renombrada"},
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_update_tipo_forbidden_without_user_id(client_with_conn) -> None:
    """Fail-closed sobre la IDENTIDAD: sin X-User-Id no hay usuario que
    resolver → 403, aunque la BD "tuviera" el permiso."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True

    resp = await client.patch(
        f"/api/v1/tipos-documentales/{uuid.uuid4()}",
        json={"nombre": "Resolución renombrada"},
        headers={"X-Tenant-Slug": TENANT_HEADERS["X-Tenant-Slug"]},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    mock_conn.fetchrow.assert_not_called()


@pytest.mark.asyncio
async def test_delete_tipo_forbidden_without_permission(client_with_conn) -> None:
    """delete_tipo (DELETE) también está gateado: sin USUA_PERM_TRD,
    `require_permission` corta con 403 antes de tocar la lógica de negocio."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = False  # permiso denegado

    resp = await client.delete(
        f"/api/v1/tipos-documentales/{uuid.uuid4()}",
        headers=TENANT_HEADERS,
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    # `require_permission` sí llama `execute` (SET search_path) antes de
    # denegar; solo debe haberse llamado esa vez, no el DELETE de negocio.
    mock_conn.execute.assert_called_once()


@pytest.mark.asyncio
async def test_delete_tipo_forbidden_without_user_id(client_with_conn) -> None:
    """Fail-closed sobre la IDENTIDAD: sin X-User-Id no hay usuario que
    resolver → 403, aunque la BD "tuviera" el permiso."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True

    resp = await client.delete(
        f"/api/v1/tipos-documentales/{uuid.uuid4()}",
        headers={"X-Tenant-Slug": TENANT_HEADERS["X-Tenant-Slug"]},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"
    # `require_permission` sí llama `execute` (SET search_path) antes de
    # denegar; solo debe haberse llamado esa vez, no el DELETE de negocio.
    mock_conn.execute.assert_called_once()


@pytest.mark.asyncio
async def test_create_tipo_writes_audit_entry(client_with_conn) -> None:
    """Media (RBAC follow-up): crear un tipo documental deja traza en
    `audit_log` con el actor (X-User-Id) y el código del tipo."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # USUA_PERM_TRD granted (gate)
    tid = uuid.uuid4()
    mock_conn.fetchrow.return_value = _tipo_row(tid, code="RES")

    resp = await client.post(
        "/api/v1/tipos-documentales",
        json={"code": "RES", "nombre": "Resolución"},
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
    assert audit_calls[0][5] == "archive.tipo_documental_created"
    assert audit_calls[0][6] == "tipo_documental"
    assert audit_calls[0][7] == "RES"


@pytest.mark.asyncio
async def test_update_tipo_writes_audit_entry(client_with_conn) -> None:
    """Media (RBAC follow-up): actualizar un tipo documental deja traza en
    `audit_log`."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # USUA_PERM_TRD granted (gate)
    tid = uuid.uuid4()
    mock_conn.fetchrow.return_value = _tipo_row(tid, nombre="Resolución renombrada")

    resp = await client.patch(
        f"/api/v1/tipos-documentales/{tid}",
        json={"nombre": "Resolución renombrada"},
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
    assert audit_calls[0][5] == "archive.tipo_documental_updated"
    assert audit_calls[0][6] == "tipo_documental"
    assert json.loads(audit_calls[0][8])["changes"] == {"nombre": "Resolución renombrada"}


@pytest.mark.asyncio
async def test_delete_tipo_writes_audit_entry(client_with_conn) -> None:
    """Media (RBAC follow-up): borrar un tipo documental deja traza en
    `audit_log`; sin fila borrada (404) no debe auditarse nada."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # USUA_PERM_TRD granted (gate)
    tid = uuid.uuid4()
    mock_conn.execute.return_value = "DELETE 1"

    resp = await client.delete(
        f"/api/v1/tipos-documentales/{tid}",
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 204

    audit_calls = [
        c.args
        for c in mock_conn.execute.call_args_list
        if isinstance(c.args[0], str) and "INSERT INTO public.audit_log" in c.args[0]
    ]
    assert len(audit_calls) == 1
    assert audit_calls[0][3] == TENANT_HEADERS["X-User-Id"]
    assert audit_calls[0][5] == "archive.tipo_documental_deleted"
    assert audit_calls[0][6] == "tipo_documental"
    assert audit_calls[0][7] == str(tid)


# ---------------------------------------------------------------------------
# Mínimo privilegio (RF-SEG-03): la disposición (delete_tipo) exige crud>=3
# (Crear/Borrar); las mutaciones no-disposición (create) quedan en el default 1.
# El umbral viaja como $3 al fetchval del gate (dentro del SQL mockeado).
# ---------------------------------------------------------------------------


def _gate_min_crud(mock_conn) -> int:
    """Extrae el min_crud ($3) del fetchval del gate RBAC de entre todas las
    llamadas a fetchval (audit.append hace su propio fetchval de prev_hash, así
    que no basta con `call_args`; se filtra por la query del gate)."""
    gate_calls = [
        c.args
        for c in mock_conn.fetchval.call_args_list
        if isinstance(c.args[0], str) and "gp.crud >= $3" in c.args[0]
    ]
    assert len(gate_calls) == 1, f"esperaba 1 fetchval del gate, hubo {len(gate_calls)}"
    return gate_calls[0][3]


@pytest.mark.asyncio
async def test_delete_tipo_threads_min_crud_3(client_with_conn) -> None:
    """delete_tipo es disposición: el gate exige crud>=3 → el fetchval del
    permiso recibe min_crud=3 como 4.º argumento posicional ($3)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True  # permiso concedido
    mock_conn.execute.return_value = "DELETE 1"

    resp = await client.delete(
        f"/api/v1/tipos-documentales/{uuid.uuid4()}", headers=TENANT_HEADERS
    )
    assert resp.status_code == 204
    assert _gate_min_crud(mock_conn) == 3


@pytest.mark.asyncio
async def test_create_tipo_threads_default_min_crud_1(client_with_conn) -> None:
    """create_tipo NO es disposición: usa el default min_crud=1 (sin elevar)."""
    client, mock_conn = client_with_conn
    mock_conn.fetchval.return_value = True
    mock_conn.fetchrow.return_value = _tipo_row(uuid.uuid4())

    resp = await client.post(
        "/api/v1/tipos-documentales",
        json={"code": "RES", "nombre": "Resolución"},
        headers=TENANT_HEADERS,
    )
    assert resp.status_code == 201
    assert _gate_min_crud(mock_conn) == 1


@pytest.mark.asyncio
async def test_list_tipos_open_read_no_user_id_needed(client_with_conn) -> None:
    """Confirma que la lectura sigue ABIERTA (solo `_require_tenant`): sin
    X-User-Id ni permiso mockeado, GET /tipos-documentales sigue respondiendo
    200."""
    client, mock_conn = client_with_conn
    mock_conn.fetch.return_value = []
    mock_conn.fetchval.return_value = 0

    resp = await client.get(
        "/api/v1/tipos-documentales", headers={"X-Tenant-Slug": TENANT_HEADERS["X-Tenant-Slug"]}
    )

    assert resp.status_code == 200
