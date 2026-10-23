# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del CRUD de dependencias (E14). Mockean get_tenant_conn (sin BD real).

asyncpg.Record se sustituye por dict: el código usa dict(r) y r["col"], compatible.

Las escrituras (POST/PATCH/DELETE) exigen `USUA_PERM_ADMIN` (hallazgo Crítico de
la auditoría 2026-07) y dejan asiento Canal A; se patchea
`app.core.audit._audit.append` para no ejercer la cadena de hash real. `has_children`
(DELETE) y el gate RBAC comparten `conn.fetchval` — se secuencian con `side_effect`
cuando el test necesita distinguir ambas llamadas.
"""

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.database import get_tenant_conn
from app.main import app

_HDRS = {"X-User-Id": str(uuid.uuid4()), "X-Tenant-Slug": "demo"}


def _dep(
    *,
    codigo: int = 100,
    sigla: str = "DG",
    nombre: str = "Dirección General",
    parent_id: uuid.UUID | None = None,
    dep_id: uuid.UUID | None = None,
    depe_rad_tp2: bool = False,
    activo: bool = True,
) -> dict:
    now = datetime.now(tz=UTC)
    return {
        "id": dep_id or uuid.uuid4(),
        "parent_id": parent_id,
        "codigo": codigo,
        "sigla": sigla,
        "nombre": nombre,
        "depe_rad_tp2": depe_rad_tp2,
        "activo": activo,
        "created_at": now,
        "updated_at": now,
    }


@asynccontextmanager
async def _noop_tx() -> AsyncGenerator[None, None]:
    yield


@pytest_asyncio.fixture(scope="function")
async def client_conn() -> AsyncGenerator[tuple[AsyncClient, AsyncMock], None]:
    conn = AsyncMock()
    conn.fetch.return_value = []
    conn.fetchrow.return_value = None
    # Gate RBAC (USUA_PERM_ADMIN) concedido por defecto; `has_children` (DELETE)
    # también usa fetchval — los tests que lo necesitan distinguir usan side_effect.
    conn.fetchval.return_value = True
    conn.execute.return_value = "DELETE 0"
    conn.transaction = MagicMock(side_effect=lambda *a, **k: _noop_tx())

    async def override() -> AsyncGenerator[AsyncMock, None]:
        yield conn

    app.dependency_overrides[get_tenant_conn] = override
    with patch("app.core.audit._audit.append", new=AsyncMock(return_value="h")) as audit_append:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            ac.audit_append = audit_append  # type: ignore[attr-defined]
            yield ac, conn
    app.dependency_overrides.clear()


# --- gate RBAC ------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_dependencia_forbidden_without_permission(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, conn = client_conn
    conn.fetchval.return_value = False
    resp = await client.post(
        "/api/v1/dependencias",
        json={"codigo": 100, "sigla": "DG", "nombre": "Dirección General"},
        headers=_HDRS,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_create_dependencia_forbidden_without_identity(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, _ = client_conn
    resp = await client.post(
        "/api/v1/dependencias", json={"codigo": 100, "sigla": "DG", "nombre": "X"}
    )
    assert resp.status_code == 403


# --- create -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_dependencia(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetchrow.return_value = _dep(codigo=100, sigla="DG")
    conn.fetchval.side_effect = [True, False]  # RBAC, código no retirado
    resp = await client.post(
        "/api/v1/dependencias",
        json={"codigo": 100, "sigla": "DG", "nombre": "Dirección General"},
        headers=_HDRS,
    )
    assert resp.status_code == 201
    assert resp.json()["codigo"] == 100
    assert client.audit_append.await_args.kwargs["action"] == "dependencia.creada"
    assert client.audit_append.await_args.kwargs["actor"] == _HDRS["X-User-Id"]


@pytest.mark.asyncio
async def test_create_duplicate_codigo(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetchrow.side_effect = asyncpg.UniqueViolationError("dup")
    conn.fetchval.side_effect = [True, False]  # RBAC, código no retirado
    resp = await client.post(
        "/api/v1/dependencias",
        json={"codigo": 100, "sigla": "DG", "nombre": "X"},
        headers=_HDRS,
    )
    assert resp.status_code == 409
    client.audit_append.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_bad_parent(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetchrow.side_effect = asyncpg.ForeignKeyViolationError("fk")
    conn.fetchval.side_effect = [True, False]  # RBAC, código no retirado
    resp = await client.post(
        "/api/v1/dependencias",
        json={"codigo": 200, "sigla": "SUB", "nombre": "Sub", "parent_id": str(uuid.uuid4())},
        headers=_HDRS,
    )
    assert resp.status_code == 400
    assert resp.json()["detail"]["error"] == "invalid_parent"
    client.audit_append.assert_not_awaited()


# --- lecturas (sin gate) ------------------------------------------------------


@pytest.mark.asyncio
async def test_get_by_codigo_found(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetchrow.return_value = _dep(codigo=305, sigla="ARCH")
    resp = await client.get("/api/v1/dependencias/by-codigo/305")
    assert resp.status_code == 200
    assert resp.json()["sigla"] == "ARCH"


@pytest.mark.asyncio
async def test_get_by_codigo_not_found(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetchrow.return_value = None
    resp = await client.get("/api/v1/dependencias/by-codigo/999")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_get_by_id_not_found(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetchrow.return_value = None
    resp = await client.get(f"/api/v1/dependencias/{uuid.uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_tree_builds_hierarchy(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    root_id = uuid.uuid4()
    child_id = uuid.uuid4()
    conn.fetch.return_value = [
        _dep(codigo=100, sigla="DG", dep_id=root_id),
        _dep(codigo=110, sigla="SUB", dep_id=child_id, parent_id=root_id),
    ]
    resp = await client.get("/api/v1/dependencias/tree")
    assert resp.status_code == 200
    tree = resp.json()
    assert len(tree) == 1
    assert tree[0]["codigo"] == 100
    assert len(tree[0]["children"]) == 1
    assert tree[0]["children"][0]["codigo"] == 110


# --- update -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_partial(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    dep_id = uuid.uuid4()
    # 1ª fetchrow: get_for_update (valor previo); 2ª: UPDATE ... RETURNING
    conn.fetchrow.side_effect = [
        _dep(dep_id=dep_id),
        _dep(dep_id=dep_id, nombre="Nuevo Nombre"),
    ]
    conn.fetchval.side_effect = [True, None, None]  # RBAC + to_regclass x2
    resp = await client.patch(
        f"/api/v1/dependencias/{dep_id}", json={"nombre": "Nuevo Nombre"}, headers=_HDRS
    )
    assert resp.status_code == 200
    assert resp.json()["nombre"] == "Nuevo Nombre"
    kw = client.audit_append.await_args.kwargs
    assert kw["action"] == "dependencia.actualizada"
    assert kw["payload"]["antes"]["nombre"] == "Dirección General"
    assert kw["payload"]["despues"]["nombre"] == "Nuevo Nombre"


def _cols(*pares: tuple[str, str]) -> list[dict]:
    return [{"table_name": t, "column_name": c} for t, c in pares]


@pytest.mark.asyncio
async def test_update_rename_con_tramite_vivo_ya_no_bloquea_y_deja_historico(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """B5/ADR-032: el flujo correlaciona por CÓDIGO; renombrar con trámite vivo es 200."""
    client, conn = client_conn
    dep_id = uuid.uuid4()
    conn.fetchrow.side_effect = [
        _dep(dep_id=dep_id, nombre="Vieja", codigo=901),
        _dep(dep_id=dep_id, nombre="Nueva", codigo=901),
    ]
    conn.fetch.side_effect = lambda sql, *a: (  # el chequeo de mapeos no ve nada
        [] if "mapeo_manual" in sql else _cols(("flow_steps", "to_dept_code"))
    )
    conn.fetchval.side_effect = [True, 3]  # RBAC, y por si alguien cuenta pasos vivos
    resp = await client.patch(
        f"/api/v1/dependencias/{dep_id}", json={"nombre": "Nueva"}, headers=_HDRS
    )
    assert resp.status_code == 200
    # El nombre ANTERIOR queda resoluble para la cola de eventos fallidos.
    insert = [c for c in conn.execute.await_args_list if "dependencia_nombre_historico" in str(c.args[0])]
    assert len(insert) == 1
    assert insert[0].args[1:4] == (901, "Vieja", "renombre")
    assert not any("flow_steps" in str(c.args[0]) for c in conn.fetchval.await_args_list)
    assert client.audit_append.await_args.kwargs["payload"]["codigo"] == 901


@pytest.mark.asyncio
async def test_update_codigo_distinto_422_sin_tocar_nada(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, conn = client_conn
    dep_id = uuid.uuid4()
    conn.fetchrow.return_value = _dep(dep_id=dep_id, codigo=901)
    resp = await client.patch(
        f"/api/v1/dependencias/{dep_id}", json={"codigo": 902}, headers=_HDRS
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "dependencia_codigo_inmutable"
    assert not any("UPDATE dependencias" in str(c.args[0]) for c in conn.fetchrow.await_args_list)
    client.audit_append.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_mismo_codigo_es_noop_y_no_llega_al_update(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, conn = client_conn
    dep_id = uuid.uuid4()
    conn.fetchrow.side_effect = [_dep(dep_id=dep_id, codigo=901), _dep(dep_id=dep_id, codigo=901, sigla="XX")]
    resp = await client.patch(
        f"/api/v1/dependencias/{dep_id}", json={"codigo": 901, "sigla": "XX"}, headers=_HDRS
    )
    assert resp.status_code == 200
    update_sql = str(conn.fetchrow.await_args_list[1].args[0])
    assert "codigo" not in update_sql.split("WHERE")[0]


@pytest.mark.asyncio
async def test_update_trigger_de_codigo_se_mapea_a_422(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Defensa en profundidad: si el trigger de la 013 salta, el mismo 422."""
    client, conn = client_conn
    dep_id = uuid.uuid4()
    conn.fetchrow.side_effect = [
        _dep(dep_id=dep_id),
        asyncpg.CheckViolationError("dependencia_codigo_inmutable: no"),
    ]
    resp = await client.patch(
        f"/api/v1/dependencias/{dep_id}", json={"sigla": "ZZ"}, headers=_HDRS
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "dependencia_codigo_inmutable"


@pytest.mark.asyncio
async def test_update_ciclo_se_mapea_a_409(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    """C-4/C8: el trigger de la 015 rechaza A -> B -> A con `dependencia_ciclo`; el router lo
    traduce a 409 (no a un 500 con la excepción cruda de Postgres)."""
    client, conn = client_conn
    dep_id = uuid.uuid4()
    conn.fetchrow.side_effect = [
        _dep(dep_id=dep_id),
        asyncpg.CheckViolationError("dependencia_ciclo: el nuevo padre es descendiente"),
    ]
    resp = await client.patch(
        f"/api/v1/dependencias/{dep_id}", json={"parent_id": str(uuid.uuid4())}, headers=_HDRS
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "dependencia_ciclo"
    client.audit_append.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_update_otro_check_violation_no_se_disfraza_de_ciclo(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Control: solo el mensaje `dependencia_ciclo` produce ese 409; cualquier otro CHECK sigue
    siendo lo que es (aquí se propaga: el handler global lo convierte en 500)."""
    client, conn = client_conn
    dep_id = uuid.uuid4()
    conn.fetchrow.side_effect = [
        _dep(dep_id=dep_id),
        asyncpg.CheckViolationError("ck_dependencias_nombre_len"),
    ]
    with pytest.raises(asyncpg.CheckViolationError):
        await client.patch(f"/api/v1/dependencias/{dep_id}", json={"sigla": "ZZ"}, headers=_HDRS)


@pytest.mark.asyncio
async def test_create_ciclo_se_mapea_a_409(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetchval.side_effect = [True, False]
    conn.fetchrow.side_effect = asyncpg.CheckViolationError("dependencia_ciclo: x")
    resp = await client.post(
        "/api/v1/dependencias",
        json={"codigo": 100, "sigla": "DG", "nombre": "Dirección General"},
        headers=_HDRS,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "dependencia_ciclo"


@pytest.mark.asyncio
async def test_update_desactivar_con_regla_viva_409(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, conn = client_conn
    dep_id = uuid.uuid4()
    conn.fetchrow.return_value = _dep(dep_id=dep_id, nombre="Vieja", codigo=901)
    conn.fetch.return_value = _cols(
        ("flow_steps", "to_dept_code"), ("workflow_rules", "assign_to_dept_code")
    )
    conn.fetchval.side_effect = [True, 0, 2]  # RBAC, pasos, reglas
    resp = await client.patch(
        f"/api/v1/dependencias/{dep_id}", json={"activo": False}, headers=_HDRS
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["reglas"] == 2


@pytest.mark.asyncio
async def test_update_desactivar_con_paso_vivo_409_por_codigo(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, conn = client_conn
    dep_id = uuid.uuid4()
    conn.fetchrow.return_value = _dep(dep_id=dep_id, codigo=901)
    conn.fetch.return_value = _cols(("flow_steps", "to_dept_code"))
    conn.fetchval.side_effect = [True, 3]
    resp = await client.patch(
        f"/api/v1/dependencias/{dep_id}", json={"activo": False}, headers=_HDRS
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["pasos_vivos"] == 3
    sql, codigo = conn.fetchval.await_args_list[1].args
    assert "to_dept_code = $1" in sql and codigo == 901
    client.audit_append.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_rename_sin_workflow_200(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, conn = client_conn
    dep_id = uuid.uuid4()
    conn.fetchrow.side_effect = [_dep(dep_id=dep_id, nombre="Vieja"), _dep(dep_id=dep_id, nombre="Nueva")]
    resp = await client.patch(
        f"/api/v1/dependencias/{dep_id}", json={"nombre": "Nueva"}, headers=_HDRS
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_nombre_mayor_de_100_422(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, _ = client_conn
    resp = await client.post(
        "/api/v1/dependencias",
        json={"codigo": 100, "sigla": "DG", "nombre": "x" * 101},
        headers=_HDRS,
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_update_sin_cambio_de_nombre_no_deja_historico(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, conn = client_conn
    dep_id = uuid.uuid4()
    conn.fetchrow.return_value = _dep(dep_id=dep_id, nombre="Igual", sigla="XX")
    resp = await client.patch(f"/api/v1/dependencias/{dep_id}", json={"sigla": "XX"}, headers=_HDRS)
    assert resp.status_code == 200
    conn.execute.assert_not_awaited()


# --- mapeo manual de nombre ---------------------------------------------------


@pytest.mark.asyncio
async def test_mapeo_nombre_registra_y_asienta(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    dep_id = uuid.uuid4()
    conn.fetchrow.return_value = _dep(dep_id=dep_id, codigo=901)
    conn.fetchval.side_effect = [True, False]  # RBAC, ninguna OTRA activa lleva ese nombre
    resp = await client.post(
        f"/api/v1/dependencias/{dep_id}/mapeos-nombre",
        json={"nombre": "Archivo", "motivo": "El nombre antiguo era esta dependencia"},
        headers=_HDRS,
    )
    assert resp.status_code == 201
    assert resp.json() == {"codigo": 901, "nombre": "Archivo", "origen": "mapeo_manual"}
    insert = conn.execute.await_args
    assert "dependencia_nombre_historico" in insert.args[0]
    assert insert.args[1:4] == (901, "Archivo", "mapeo_manual")
    kw = client.audit_append.await_args.kwargs
    assert kw["action"] == "dependencia.mapeo_nombre" and kw["payload"]["codigo"] == 901


@pytest.mark.asyncio
async def test_mapeo_nombre_resuelto_a_otra_dependencia_409(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Solo se mapean nombres huérfanos o ambiguos: uno que ya resuelve limpio a OTRO código no."""
    client, conn = client_conn
    dep_id = uuid.uuid4()
    conn.fetchrow.return_value = _dep(dep_id=dep_id, codigo=901)
    conn.fetchval.side_effect = [True, False]
    conn.fetch.return_value = [{"codigo": 555, "candidatos": 1}]
    resp = await client.post(
        f"/api/v1/dependencias/{dep_id}/mapeos-nombre",
        json={"nombre": "Archivo", "motivo": "El nombre antiguo era esta dependencia"},
        headers=_HDRS,
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "nombre_resuelto_a_otra_dependencia"
    conn.execute.assert_not_awaited()
    client.audit_append.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "filas",
    [
        [],  # huérfano
        [{"codigo": None, "candidatos": 2}],  # ambiguo
        [{"codigo": 901, "candidatos": 1}],  # ya es esta misma dependencia
    ],
)
async def test_mapeo_nombre_admite_huerfano_ambiguo_o_propio(
    client_conn: tuple[AsyncClient, AsyncMock], filas: list[dict]
) -> None:
    client, conn = client_conn
    dep_id = uuid.uuid4()
    conn.fetchrow.return_value = _dep(dep_id=dep_id, codigo=901)
    conn.fetchval.side_effect = [True, False]
    conn.fetch.return_value = filas
    resp = await client.post(
        f"/api/v1/dependencias/{dep_id}/mapeos-nombre",
        json={"nombre": "Archivo", "motivo": "El nombre antiguo era esta dependencia"},
        headers=_HDRS,
    )
    assert resp.status_code == 201


@pytest.mark.asyncio
async def test_update_rename_a_nombre_mapeado_a_otro_codigo_409(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, conn = client_conn
    dep_id = uuid.uuid4()
    conn.fetchrow.return_value = _dep(dep_id=dep_id, codigo=901)
    conn.fetch.return_value = [{"codigo": 555}]
    resp = await client.patch(
        f"/api/v1/dependencias/{dep_id}", json={"nombre": "Archivo"}, headers=_HDRS
    )
    assert resp.status_code == 409
    assert resp.json()["detail"]["error"] == "nombre_mapeado_a_otra_dependencia"
    client.audit_append.assert_not_awaited()


@pytest.mark.asyncio
async def test_mapeo_nombre_exige_admin_y_motivo(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    dep_id = uuid.uuid4()
    r = await client.post(
        f"/api/v1/dependencias/{dep_id}/mapeos-nombre",
        json={"nombre": "Archivo", "motivo": "corto"},
        headers=_HDRS,
    )
    assert r.status_code == 422
    conn.fetchval.return_value = False
    r = await client.post(
        f"/api/v1/dependencias/{dep_id}/mapeos-nombre",
        json={"nombre": "Archivo", "motivo": "motivo suficientemente largo"},
        headers=_HDRS,
    )
    assert r.status_code == 403
    # El 403 deja su asiento de denegación; el acto (mapeo) no se asienta ni se escribe.
    assert all(c.kwargs.get("action") != "dependencia.mapeo_nombre" for c in client.audit_append.await_args_list)
    conn.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_mapeo_nombre_dependencia_inexistente_404(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, conn = client_conn
    conn.fetchrow.return_value = None
    r = await client.post(
        f"/api/v1/dependencias/{uuid.uuid4()}/mapeos-nombre",
        json={"nombre": "Archivo", "motivo": "motivo suficientemente largo"},
        headers=_HDRS,
    )
    assert r.status_code == 404
    client.audit_append.assert_not_awaited()


@pytest.mark.asyncio
async def test_update_self_parent_rejected(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, _ = client_conn
    dep_id = uuid.uuid4()
    resp = await client.patch(
        f"/api/v1/dependencias/{dep_id}", json={"parent_id": str(dep_id)}, headers=_HDRS
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_update_not_found(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetchrow.return_value = None
    resp = await client.patch(
        f"/api/v1/dependencias/{uuid.uuid4()}", json={"nombre": "X"}, headers=_HDRS
    )
    assert resp.status_code == 404
    client.audit_append.assert_not_awaited()


# --- delete -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_with_children_conflict(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    # 1ª llamada a fetchval: gate RBAC (concedido); 2ª: has_children → True
    conn.fetchval.side_effect = [True, 1]
    conn.fetchrow.return_value = _dep()  # has_children corre dentro de la tx, tras bloquear la fila
    resp = await client.delete(f"/api/v1/dependencias/{uuid.uuid4()}", headers=_HDRS)
    assert resp.status_code == 409
    client.audit_append.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_ok(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    # RBAC (concedido); has_children → sin hijos; to_regclass del historial → ausente
    conn.fetchval.side_effect = [True, None, None]
    conn.fetchrow.return_value = _dep(nombre="Vieja")
    conn.execute.return_value = "DELETE 1"
    resp = await client.delete(f"/api/v1/dependencias/{uuid.uuid4()}", headers=_HDRS)
    assert resp.status_code == 204
    kw = client.audit_append.await_args.kwargs
    assert kw["action"] == "dependencia.eliminada"
    retirada = [c for c in conn.execute.await_args_list if "dependencia_codigo_retirado" in str(c.args[0])]
    assert len(retirada) == 1 and retirada[0].args[1:3] == (100, "Vieja")
    assert kw["payload"]["antes"]["nombre"] == "Vieja"


@pytest.mark.asyncio
async def test_delete_con_historia_409(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    """Una dependencia usada (aunque solo sea historia) no se borra: se desactiva."""
    client, conn = client_conn
    conn.fetchrow.return_value = _dep(nombre="Vieja", codigo=901)
    conn.fetch.return_value = _cols(("flow_events", "to_dept_code"))
    # RBAC, has_children, to_regclass del historial (ausente), filas de flow_events
    conn.fetchval.side_effect = [True, None, None, 4]
    resp = await client.delete(f"/api/v1/dependencias/{uuid.uuid4()}", headers=_HDRS)
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["error"] == "dependencia_con_historia"
    assert detail["referencias"] == {"flow_events.to_dept_code": 4}
    conn.execute.assert_not_awaited()
    client.audit_append.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_not_found(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetchval.side_effect = [True, None]
    conn.fetchrow.return_value = None
    resp = await client.delete(f"/api/v1/dependencias/{uuid.uuid4()}", headers=_HDRS)
    assert resp.status_code == 404
    client.audit_append.assert_not_awaited()
