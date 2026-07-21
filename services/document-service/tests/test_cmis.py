# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Tests del perfil CMIS mínimo de lectura (E11, RF-INT-02). Mock del pool
(`app.state.db_pool`) como en OAI/postal. El recorte public-only se ejerce en SQL y
se valida contra Postgres real en el smoke; aquí, el Browser Binding y la
indistinguibilidad (id reservado → 404 idéntico a inexistente)."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from app.main import app


def _rad(tn: str = "2026-DEMO-E-000001", subject: str = "Acta pública") -> dict:
    return {
        "tracking_number": tn,
        "doc_type": "E",
        "subject": subject,
        "origin_dept": "Secretaría General",
        "registered_at": datetime(2026, 3, 1, 10, 0, 0, tzinfo=UTC),
        "tenant_code": "DEMO",
    }


class _FakePool:
    """El router CMIS usa `await pool.acquire()` + `await pool.release(conn)`."""

    def __init__(self, conn) -> None:
        self._conn = conn

    async def acquire(self):
        return self._conn

    async def release(self, conn) -> None:
        return None


@pytest_asyncio.fixture
async def cmis_ctx() -> AsyncGenerator[tuple[AsyncClient, AsyncMock], None]:
    conn = AsyncMock()
    conn.execute.return_value = None
    conn.fetch.return_value = []
    conn.fetchrow.return_value = None
    conn.fetchval.return_value = 1  # to_regclass no-None por defecto (tenant existe)
    app.state.db_pool = _FakePool(conn)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac, conn
    if hasattr(app.state, "db_pool"):
        delattr(app.state, "db_pool")


@pytest.mark.asyncio
async def test_repository_info(cmis_ctx: tuple[AsyncClient, AsyncMock]) -> None:
    client, _ = cmis_ctx
    resp = await client.get("/api/v1/public/cmis/demo")
    assert resp.status_code == 200
    body = resp.json()
    repo = body["orpyca:demo"]
    assert repo["cmisVersionSupported"] == "1.1"
    assert repo["rootFolderId"] == "root"
    assert repo["capabilities"]["capabilityQuery"] == "none"
    # #1b: URLs del Browser Binding (CMIS 1.1 §5.1) presentes.
    assert repo["repositoryUrl"].endswith("/api/v1/public/cmis/demo")
    assert repo["rootFolderUrl"].endswith("/api/v1/public/cmis/demo/root")


@pytest.mark.asyncio
async def test_get_children(cmis_ctx: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = cmis_ctx
    conn.fetchval.side_effect = [1, 2]  # to_regclass, count_public
    conn.fetch.return_value = [_rad(), _rad(tn="2026-DEMO-E-000002")]
    resp = await client.get("/api/v1/public/cmis/demo/root?cmisselector=children")
    assert resp.status_code == 200
    body = resp.json()
    assert body["numItems"] == 2
    ids = [o["object"]["properties"]["cmis:objectId"]["value"] for o in body["objects"]]
    assert ids == ["2026-DEMO-E-000001", "2026-DEMO-E-000002"]


@pytest.mark.asyncio
async def test_get_object_public(cmis_ctx: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = cmis_ctx
    conn.fetchrow.return_value = _rad(subject="Convenio")
    resp = await client.get(
        "/api/v1/public/cmis/demo/root?cmisselector=object&objectId=2026-DEMO-E-000001"
    )
    assert resp.status_code == 200
    props = resp.json()["properties"]
    assert props["cmis:objectId"]["value"] == "2026-DEMO-E-000001"
    assert props["cmis:name"]["value"] == "Convenio"
    assert props["cmis:baseTypeId"]["value"] == "cmis:document"


@pytest.mark.asyncio
async def test_get_object_root_folder(cmis_ctx: tuple[AsyncClient, AsyncMock]) -> None:
    client, _ = cmis_ctx
    resp = await client.get("/api/v1/public/cmis/demo/root?cmisselector=object&objectId=root")
    assert resp.status_code == 200
    assert resp.json()["properties"]["cmis:baseTypeId"]["value"] == "cmis:folder"


@pytest.mark.asyncio
async def test_get_object_reserved_is_404(cmis_ctx: tuple[AsyncClient, AsyncMock]) -> None:
    """Id reservado/inexistente → 404 objectNotFound INDISTINGUIBLE (get_public=None)."""
    client, conn = cmis_ctx
    conn.fetchrow.return_value = None
    resp = await client.get(
        "/api/v1/public/cmis/demo/root?cmisselector=object&objectId=2026-DEMO-S-000099"
    )
    assert resp.status_code == 404
    assert resp.json()["exception"] == "objectNotFound"


@pytest.mark.asyncio
async def test_content_requires_object_id(cmis_ctx: tuple[AsyncClient, AsyncMock]) -> None:
    client, _ = cmis_ctx
    resp = await client.get("/api/v1/public/cmis/demo/root?cmisselector=content")
    assert resp.status_code == 400
    assert resp.json()["exception"] == "invalidArgument"


@pytest.mark.asyncio
async def test_content_reserved_or_no_anexo_is_404(cmis_ctx: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = cmis_ctx
    conn.fetchrow.return_value = None  # get_public_anexo → None
    resp = await client.get(
        "/api/v1/public/cmis/demo/root?cmisselector=content&objectId=2026-DEMO-S-000099"
    )
    assert resp.status_code == 404
    assert resp.json()["exception"] == "objectNotFound"


@pytest.mark.asyncio
async def test_content_stream_ok(cmis_ctx: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = cmis_ctx
    conn.fetchrow.return_value = {
        "file_id": "11111111-1111-1111-1111-111111111111",
        "filename": "acta.pdf",
        "file_size": 9,
        "mime_type": "application/pdf",
    }

    class _FakeStream:
        async def aiter_bytes(self):
            yield b"PDFBYTES!"

        async def aclose(self):
            return None

    with patch(
        "app.routers.cmis.storage_client.open_content_stream",
        new=AsyncMock(return_value=_FakeStream()),
    ):
        resp = await client.get(
            "/api/v1/public/cmis/demo/root?cmisselector=content&objectId=2026-DEMO-E-000001"
        )
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert resp.content == b"PDFBYTES!"


@pytest.mark.asyncio
async def test_nonexistent_tenant_is_404(cmis_ctx: tuple[AsyncClient, AsyncMock]) -> None:
    client, conn = cmis_ctx
    conn.fetchval.side_effect = [None]  # to_regclass → NULL (tenant inexistente)
    resp = await client.get("/api/v1/public/cmis/demo")
    assert resp.status_code == 404
    assert resp.json()["exception"] == "objectNotFound"


@pytest.mark.asyncio
async def test_unsupported_selector(cmis_ctx: tuple[AsyncClient, AsyncMock]) -> None:
    client, _ = cmis_ctx
    resp = await client.get("/api/v1/public/cmis/demo/root?cmisselector=query")
    assert resp.status_code == 405  # #5: notSupported -> 405
    assert resp.json()["exception"] == "notSupported"


@pytest.mark.asyncio
async def test_content_disposition_sanitized(cmis_ctx: tuple[AsyncClient, AsyncMock]) -> None:
    """#1: un filename con comillas/CRLF/no-ascii no rompe la cabecera ni provoca 500."""
    client, conn = cmis_ctx
    conn.fetchrow.return_value = {
        "file_id": "11111111-1111-1111-1111-111111111111",
        "filename": 'e"vil\r\nSet-Cookie: x=1—café.pdf',
        "file_size": 3,
        "mime_type": "application/pdf\r\nX-Inject: 1",  # mime malicioso también
    }

    class _FakeStream:
        async def aiter_bytes(self):
            yield b"abc"

        async def aclose(self):
            return None

    with patch(
        "app.routers.cmis.storage_client.open_content_stream",
        new=AsyncMock(return_value=_FakeStream()),
    ):
        resp = await client.get(
            "/api/v1/public/cmis/demo/root?cmisselector=content&objectId=2026-DEMO-E-000001"
        )
    assert resp.status_code == 200
    cd = resp.headers["content-disposition"]
    assert "\r" not in cd and "\n" not in cd  # sin CRLF -> sin response splitting
    assert 'filename="evil' in cd  # comillas y saltos removidos del segmento ascii
    # el mime malicioso se descartó al patrón seguro
    assert resp.headers["content-type"] == "application/octet-stream"
