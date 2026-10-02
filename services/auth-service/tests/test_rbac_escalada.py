# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Cierre de la escalada por RBAC (AREA-01-01, AREA-01-N3, AREA-01-15).

`USUA_PERM_ADMIN` a nivel 1 (LEER) bastaba para administrar RBAC: quien lo recibía
solo para consultar la auditoría podía crear usuarios, meterse en grupos y
concederse permisos. Y entrar en un grupo de techo de clearance mayor subía el
clearance propio sin pasar por el no-write-up de `PUT /groups/{g}/clearance`.
"""

import uuid
from unittest.mock import AsyncMock

import pytest
from httpx import AsyncClient

from app.core.authz import Caller, get_caller
from app.main import app
from app.repositories.clearance_repository import MAX_LEVEL
from tests.test_rbac import TENANT_HEADERS, _override_conn, _patch_audit

_LECTOR = Caller(
    user_id=uuid.uuid4(),
    keycloak_sub="kc-lector",
    is_root=False,
    permissions={"USUA_PERM_ADMIN": 1},
)
_G, _U, _P = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

_ESCRITURAS = [
    ("post", "/api/v1/auth/users", {"username": "x", "email": "x@x.co"}),
    ("post", "/api/v1/auth/groups", {"name": "G"}),
    ("patch", f"/api/v1/auth/groups/{_G}", {"name": "G2"}),
    ("delete", f"/api/v1/auth/groups/{_G}", None),
    ("post", f"/api/v1/auth/groups/{_G}/members/{_U}", None),
    ("delete", f"/api/v1/auth/groups/{_G}/members/{_U}", None),
    ("put", f"/api/v1/auth/groups/{_G}/permissions/{_P}", {"crud": 3}),
    ("put", f"/api/v1/auth/groups/{_G}/clearance", {"max_level": 1}),
    ("post", f"/api/v1/auth/users/{_U}/urd", {"depe_id": 1, "rol_id": str(_G)}),
    ("delete", f"/api/v1/auth/users/{_U}/urd/{uuid.uuid4()}", None),
    ("post", f"/api/v1/auth/users/{_U}/totp/revoke", {"motivo": "perdió el autenticador"}),
    ("post", f"/api/v1/auth/admin/users/{_U}/signing-key/revoke", {"motivo": "comprometida"}),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(("metodo", "ruta", "cuerpo"), _ESCRITURAS)
async def test_admin_a_nivel_de_lectura_no_puede_escribir(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    metodo: str,
    ruta: str,
    cuerpo: dict | None,
) -> None:
    mock_append = _patch_audit(monkeypatch)
    conn = AsyncMock()
    _override_conn(conn)
    app.dependency_overrides[get_caller] = lambda: _LECTOR

    resp = await client.request(metodo.upper(), ruta, json=cuerpo, headers=TENANT_HEADERS)

    assert resp.status_code == 403, resp.text
    # El 403 viene DEL GATE (no de otra regla: p. ej. el no-write-up del PUT de
    # clearance también da 403 y taparía un gate ausente).
    assert "nivel 1" in resp.text and "nivel 3 o superior" in resp.text, resp.text
    # Ningún asiento rbac.* de éxito, y la sentencia de escritura no llegó a correr.
    acciones = [c.kwargs.get("action") for c in mock_append.call_args_list]
    assert not [a for a in acciones if a and a.startswith(("rbac.", "urd.", "totp.", "signing_key."))]
    conn.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_a_nivel_de_lectura_si_puede_leer(client: AsyncClient) -> None:
    """Control positivo: el nivel 1 sigue sirviendo para las LECTURAS."""
    conn = AsyncMock()
    conn.fetch.return_value = []
    _override_conn(conn)
    app.dependency_overrides[get_caller] = lambda: _LECTOR

    resp = await client.get("/api/v1/auth/groups", headers=TENANT_HEADERS)

    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_admin_nivel_3_pasa_el_gate(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Anti-vacuidad del gate: con nivel 3 la misma escritura NO da 403."""
    _patch_audit(monkeypatch)
    conn = AsyncMock()
    conn.fetchrow.return_value = {"id": _G, "name": "G", "description": None}
    _override_conn(conn)
    app.dependency_overrides[get_caller] = lambda: Caller(
        user_id=uuid.uuid4(), keycloak_sub="kc", is_root=False, permissions={"USUA_PERM_ADMIN": 3}
    )

    resp = await client.post("/api/v1/auth/groups", json={"name": "G"}, headers=TENANT_HEADERS)

    assert resp.status_code == 201


# --- N3: no-write-up al entrar en un grupo ------------------------------------
def _admin_no_root(nivel: int = 3) -> Caller:
    return Caller(
        user_id=uuid.uuid4(),
        keycloak_sub="kc-nr",
        is_root=False,
        permissions={"USUA_PERM_ADMIN": nivel},
    )


@pytest.mark.asyncio
async def test_agregarse_a_un_grupo_de_mayor_clearance_da_403_sin_asiento(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_append = _patch_audit(monkeypatch)
    conn = AsyncMock()
    # caller_max_level -> 1; INSERT guardado -> 0 filas; la membresía no existía.
    conn.fetchval.side_effect = [1, 0, None]
    _override_conn(conn)
    app.dependency_overrides[get_caller] = lambda: _admin_no_root()

    resp = await client.post(f"/api/v1/auth/groups/{_G}/members/{_U}", headers=TENANT_HEADERS)

    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "no_write_up"
    mock_append.assert_not_awaited()


@pytest.mark.asyncio
async def test_el_gate_de_clearance_viaja_en_el_insert(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """El nivel y el bypass ROOT son parámetros de la MISMA sentencia que inserta."""
    _patch_audit(monkeypatch)
    conn = AsyncMock()
    conn.fetchval.side_effect = [2, 1]  # caller_max_level=2; fila insertada
    _override_conn(conn)
    caller = _admin_no_root()
    app.dependency_overrides[get_caller] = lambda: caller

    resp = await client.post(f"/api/v1/auth/groups/{_G}/members/{_U}", headers=TENANT_HEADERS)

    assert resp.status_code == 201
    sql, *args = conn.fetchval.call_args_list[1].args
    assert "role_clearance" in sql and "WHERE $3::bool" in sql
    assert args == [_U, _G, False, 2]


@pytest.mark.asyncio
async def test_root_puede_agregar_a_cualquier_grupo(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Control positivo: ROOT entra sin consultar su clearance."""
    _patch_audit(monkeypatch)
    conn = AsyncMock()
    conn.fetchval.side_effect = [1]
    _override_conn(conn)  # el caller por defecto es ROOT

    resp = await client.post(f"/api/v1/auth/groups/{_G}/members/{_U}", headers=TENANT_HEADERS)

    assert resp.status_code == 201
    assert conn.fetchval.call_args.args[1:] == (_U, _G, True, MAX_LEVEL)


@pytest.mark.asyncio
async def test_ya_ser_miembro_no_es_error(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_audit(monkeypatch)
    conn = AsyncMock()
    conn.fetchval.side_effect = [3, 0, 1]  # nivel; nada insertado; ya existía
    _override_conn(conn)
    app.dependency_overrides[get_caller] = lambda: _admin_no_root()

    resp = await client.post(f"/api/v1/auth/groups/{_G}/members/{_U}", headers=TENANT_HEADERS)

    assert resp.status_code == 201


# --- 01-15: PATCH / DELETE de grupo -------------------------------------------
@pytest.mark.asyncio
async def test_delete_grupo_con_miembros_da_409(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_append = _patch_audit(monkeypatch)
    conn = AsyncMock()
    conn.fetchval.return_value = None  # DELETE ... RETURNING no borró nada
    conn.fetchrow.return_value = {"id": _G, "name": "G", "description": None}  # existe
    _override_conn(conn)

    resp = await client.delete(f"/api/v1/auth/groups/{_G}", headers=TENANT_HEADERS)

    assert resp.status_code == 409
    mock_append.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_grupo_vacio_da_204_con_asiento(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_append = _patch_audit(monkeypatch)
    conn = AsyncMock()
    conn.fetchval.return_value = _G
    conn.fetchrow.return_value = {"id": _G, "name": "Archivo", "description": "d"}
    _override_conn(conn)

    resp = await client.delete(f"/api/v1/auth/groups/{_G}", headers=TENANT_HEADERS)

    assert resp.status_code == 204
    mock_append.assert_awaited_once()
    assert mock_append.call_args.kwargs["action"] == "rbac.grupo_eliminado"
    # El asiento conserva lo borrado: nombre y descripcion reconstruibles.
    assert mock_append.call_args.kwargs["payload"] == {"name": "Archivo", "description": "d"}


@pytest.mark.asyncio
async def test_delete_grupo_inexistente_da_404(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _patch_audit(monkeypatch)
    conn = AsyncMock()
    conn.fetchval.return_value = None
    conn.fetchrow.return_value = None
    _override_conn(conn)

    resp = await client.delete(f"/api/v1/auth/groups/{_G}", headers=TENANT_HEADERS)

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_patch_grupo_actualiza_y_asienta(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_append = _patch_audit(monkeypatch)
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        {"id": _G, "name": "Viejo", "description": None},  # antes (FOR UPDATE)
        {"id": _G, "name": "Nuevo", "description": None},  # UPDATE ... RETURNING
    ]
    _override_conn(conn)

    resp = await client.patch(
        f"/api/v1/auth/groups/{_G}", json={"name": "Nuevo"}, headers=TENANT_HEADERS
    )

    assert resp.status_code == 200
    assert resp.json()["name"] == "Nuevo"
    assert mock_append.call_args.kwargs["action"] == "rbac.grupo_actualizado"
    assert mock_append.call_args.kwargs["payload"] == {
        "campos": ["name"],
        "antes": {"name": "Viejo"},
        "despues": {"name": "Nuevo"},
    }
    sql = conn.fetchrow.call_args.args[0]
    assert sql.startswith("UPDATE auth_groups SET name = $2 WHERE id = $1")


@pytest.mark.asyncio
async def test_patch_grupo_vacio_da_400_y_duplicado_409(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncpg

    _patch_audit(monkeypatch)
    conn = AsyncMock()
    _override_conn(conn)
    vacio = await client.patch(f"/api/v1/auth/groups/{_G}", json={}, headers=TENANT_HEADERS)
    assert vacio.status_code == 400

    conn.fetchrow.side_effect = asyncpg.UniqueViolationError("dup")
    dup = await client.patch(
        f"/api/v1/auth/groups/{_G}", json={"name": "Otro"}, headers=TENANT_HEADERS
    )
    assert dup.status_code == 409
