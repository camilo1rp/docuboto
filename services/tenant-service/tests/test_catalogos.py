# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests de catálogos de referencia (E14). Mockean get_tenant_conn (sin BD real).

Las escrituras (POST/PATCH/DELETE) exigen `USUA_PERM_ADMIN` (hallazgo Crítico de
la auditoría 2026-07: el servicio no tenía NINGÚN gate de autorización) y dejan
asiento Canal A (`public.audit_log`, ADR-008) en la misma transacción — se
patchea `app.core.audit._audit.append` para no ejercer la cadena de hash real
contra el mock. `conn.fetchval` gobierna el gate RBAC (primera llamada); los
tests 403 fijan `fetchval=False` (fail-closed real, mismo patrón que
document-service).
"""

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.core.database import get_tenant_conn
from app.main import app

_HDRS = {"X-User-Id": str(uuid.uuid4()), "X-Tenant-Slug": "demo"}


def _item(codigo: str = "VEN", nombre: str = "Ventanilla", activo: bool = True) -> dict:
    return {"id": uuid.uuid4(), "codigo": codigo, "nombre": nombre, "activo": activo}


@asynccontextmanager
async def _noop_tx() -> AsyncGenerator[None, None]:
    yield


@pytest_asyncio.fixture(scope="function")
async def client_conn() -> AsyncGenerator[tuple[AsyncClient, AsyncMock], None]:
    conn = AsyncMock()
    conn.fetch.return_value = []
    conn.fetchrow.return_value = None
    # Gate RBAC (USUA_PERM_ADMIN) concedido por defecto; los tests 403 lo
    # sobreescriben a False. Los GET no lo consultan (sin gate).
    conn.fetchval.return_value = True
    conn.execute.return_value = "DELETE 0"
    # `async with conn.transaction()` debe ser un CM async reentrante.
    conn.transaction = MagicMock(side_effect=lambda *a, **k: _noop_tx())

    async def override() -> AsyncGenerator[AsyncMock, None]:
        yield conn

    app.dependency_overrides[get_tenant_conn] = override
    with patch("app.core.audit._audit.append", new=AsyncMock(return_value="h")) as audit_append:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            ac.audit_append = audit_append  # type: ignore[attr-defined]
            yield ac, conn
    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_list_catalog_names(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, _ = client_conn
    resp = await client.get("/api/v1/catalogos")
    assert resp.status_code == 200
    assert "medios-recepcion" in resp.json()


@pytest.mark.asyncio
async def test_causales_catalog(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    """Catálogo de causales (devolución/anulación/reasignación) — paridad legado."""
    client, conn = client_conn
    listed = await client.get("/api/v1/catalogos")
    assert "causales" in listed.json()
    conn.fetch.return_value = [_item(codigo="DEV-DEP", nombre="Otra dependencia")]
    resp = await client.get("/api/v1/catalogos/causales")
    assert resp.status_code == 200
    assert resp.json()[0]["codigo"] == "DEV-DEP"


@pytest.mark.asyncio
async def test_unknown_catalog_404(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, _ = client_conn
    resp = await client.get("/api/v1/catalogos/no-existe")
    assert resp.status_code == 404
    assert resp.json()["detail"]["error"] == "unknown_catalog"


@pytest.mark.asyncio
async def test_list_items(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetch.return_value = [_item(), _item(codigo="COR", nombre="Correo")]
    resp = await client.get("/api/v1/catalogos/medios-recepcion")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


# --- gate RBAC ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_item_forbidden_without_permission(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, conn = client_conn
    conn.fetchval.return_value = False
    resp = await client.post(
        "/api/v1/catalogos/medios-recepcion",
        json={"codigo": "WHA", "nombre": "WhatsApp"},
        headers=_HDRS,
    )
    assert resp.status_code == 403
    assert resp.json()["detail"]["error"] == "forbidden"


@pytest.mark.asyncio
async def test_create_item_forbidden_without_identity(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    """Fail-closed: sin X-User-Id no hay identidad que demuestre el permiso."""
    client, _ = client_conn
    resp = await client.post(
        "/api/v1/catalogos/medios-recepcion", json={"codigo": "WHA", "nombre": "WhatsApp"}
    )
    assert resp.status_code == 403


# --- create ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_item(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetchrow.return_value = _item(codigo="WHA", nombre="WhatsApp")
    resp = await client.post(
        "/api/v1/catalogos/medios-recepcion",
        json={"codigo": "WHA", "nombre": "WhatsApp"},
        headers=_HDRS,
    )
    assert resp.status_code == 201
    assert resp.json()["codigo"] == "WHA"
    # asiento Canal A (public.audit_log), no solo la fila del catálogo
    client.audit_append.assert_awaited_once()
    assert client.audit_append.await_args.kwargs["action"] == "catalogo.item_creado"
    assert client.audit_append.await_args.kwargs["actor"] == _HDRS["X-User-Id"]


@pytest.mark.asyncio
async def test_create_duplicate_409(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetchrow.side_effect = asyncpg.UniqueViolationError("dup")
    resp = await client.post(
        "/api/v1/catalogos/medios-recepcion",
        json={"codigo": "VEN", "nombre": "X"},
        headers=_HDRS,
    )
    assert resp.status_code == 409
    client.audit_append.assert_not_awaited()


# --- update -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_update_item(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetchrow.return_value = _item(codigo="VEN", nombre="Ventanilla Única", activo=False)
    resp = await client.patch(
        f"/api/v1/catalogos/medios-recepcion/{uuid.uuid4()}",
        json={"nombre": "Ventanilla Única", "activo": False},
        headers=_HDRS,
    )
    assert resp.status_code == 200
    assert resp.json()["activo"] is False
    assert client.audit_append.await_args.kwargs["action"] == "catalogo.item_actualizado"


@pytest.mark.asyncio
async def test_update_item_not_found(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetchrow.return_value = None
    resp = await client.patch(
        f"/api/v1/catalogos/medios-recepcion/{uuid.uuid4()}",
        json={"nombre": "X"},
        headers=_HDRS,
    )
    assert resp.status_code == 404
    client.audit_append.assert_not_awaited()


# --- delete -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_item(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.execute.return_value = "DELETE 1"
    resp = await client.delete(f"/api/v1/catalogos/tipos-anexo/{uuid.uuid4()}", headers=_HDRS)
    assert resp.status_code == 204
    assert client.audit_append.await_args.kwargs["action"] == "catalogo.item_eliminado"


@pytest.mark.asyncio
async def test_delete_item_not_found(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.execute.return_value = "DELETE 0"
    resp = await client.delete(f"/api/v1/catalogos/tipos-anexo/{uuid.uuid4()}", headers=_HDRS)
    assert resp.status_code == 404
    client.audit_append.assert_not_awaited()


# --- ámbito de las causales (migración tenant 008) ----------------------------
#
# El acto al que pertenece una causal estaba escrito dos veces (prefijo del código
# y prefijo del nombre) y declarado ninguna, así que ninguna pantalla podía
# filtrarlo sin adivinar. Estas pruebas fijan que el eje sea de verdad un campo:
# que se pueda pedir, que se pueda escribir, y —lo importante— que pedirlo donde
# no existe FALLE en vez de devolver la lista entera como si viniera filtrada.


def _causal(codigo: str = "DEV-INC", ambito: str = "devolucion") -> dict:
    return {
        "id": uuid.uuid4(),
        "codigo": codigo,
        "nombre": "Información incompleta",
        "activo": True,
        "ambito": ambito,
    }


@pytest.mark.asyncio
async def test_causales_expone_ambito(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetch.return_value = [_causal()]
    resp = await client.get("/api/v1/catalogos/causales")
    assert resp.status_code == 200
    assert resp.json()[0]["ambito"] == "devolucion"


@pytest.mark.asyncio
async def test_causales_filtra_por_ambito(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetch.return_value = [_causal()]
    resp = await client.get("/api/v1/catalogos/causales?ambito=devolucion&activo=true")
    assert resp.status_code == 200
    # El filtro llega al SQL, no se aplica después: la consulta lleva su predicado
    # y el parámetro. Comprobar solo la respuesta pasaría con un filtro inexistente.
    sql = conn.fetch.await_args.args[0]
    assert "ambito = $" in sql
    assert "devolucion" in conn.fetch.await_args.args[1:]


@pytest.mark.asyncio
async def test_ambito_en_otro_catalogo_422(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    """Pedir ámbito donde no existe se RECHAZA; ignorarlo devolvería todo."""
    client, conn = client_conn
    conn.fetch.return_value = [_item()]
    resp = await client.get("/api/v1/catalogos/medios-recepcion?ambito=devolucion")
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "ambito_no_aplica"
    conn.fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_crear_causal_con_ambito(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = client_conn
    conn.fetchrow.return_value = _causal(codigo="DEV-XXX")
    resp = await client.post(
        "/api/v1/catalogos/causales",
        json={"codigo": "DEV-XXX", "nombre": "Otra", "ambito": "devolucion"},
        headers=_HDRS,
    )
    assert resp.status_code == 201
    assert resp.json()["ambito"] == "devolucion"
    assert "ambito" in conn.fetchrow.await_args.args[0]


@pytest.mark.asyncio
async def test_crear_con_ambito_en_otro_catalogo_422(
    client_conn: tuple[AsyncClient, AsyncMock],
) -> None:
    client, conn = client_conn
    resp = await client.post(
        "/api/v1/catalogos/medios-recepcion",
        json={"codigo": "COR", "nombre": "Correo", "ambito": "devolucion"},
        headers=_HDRS,
    )
    assert resp.status_code == 422
    assert resp.json()["detail"]["error"] == "ambito_no_aplica"
    conn.fetchrow.assert_not_awaited()


@pytest.mark.asyncio
async def test_ambito_invalido_422(client_conn: tuple[AsyncClient, AsyncMock]) -> None:
    """El conjunto de valores lo fija el esquema, no la buena voluntad del cliente."""
    client, _ = client_conn
    resp = await client.post(
        "/api/v1/catalogos/causales",
        json={"codigo": "X", "nombre": "Y", "ambito": "inventado"},
        headers=_HDRS,
    )
    assert resp.status_code == 422
