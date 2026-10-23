# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.


"""`/auth/me` publica `asistente_habilitado` (ADR-037) y falla cerrado.

La UI y mcp-server toman la bandera de aquí; knowledge-service la vuelve a leer de la
base. Lo que importa es que un error de lectura NUNCA se vea como «encendido».
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest
from httpx import AsyncClient

from app.core.database import get_optional_tenant_conn
from app.main import app
from app.schemas.auth import UserInfoResponse

pytestmark = pytest.mark.asyncio


def _conn(bandera: object) -> AsyncMock:
    conn = AsyncMock()
    conn.fetchrow.return_value = None  # usuario no aprovisionado: la bandera es de la entidad
    sql_vistas: list[str] = []

    async def _fetchval(sql: str, *a: object) -> object:
        sql_vistas.append(sql)
        if "asistente_habilitado" in sql:
            if isinstance(bandera, Exception):
                raise bandera
            return bandera
        return False

    conn.fetchval.side_effect = _fetchval
    return conn


async def _me(client: AsyncClient, conn: AsyncMock | None, slug: str | None = "demo") -> dict:
    async def _dep():
        yield conn

    app.dependency_overrides[get_optional_tenant_conn] = _dep
    info = UserInfoResponse(
        user_id=str(uuid.uuid4()), username="u", email="u@x", tenant_slug=slug, roles=[]
    )
    try:
        with patch(
            "app.services.auth_service.AuthService.get_current_user",
            new_callable=AsyncMock,
            return_value=info,
        ):
            resp = await client.get(
                "/api/v1/auth/me",
                headers={"Authorization": "Bearer x", "X-Tenant-Slug": "demo"},
            )
    finally:
        app.dependency_overrides.pop(get_optional_tenant_conn, None)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_me_trae_la_bandera_encendida(client: AsyncClient) -> None:
    assert (await _me(client, _conn(True)))["asistente_habilitado"] is True


async def test_me_trae_la_bandera_apagada(client: AsyncClient) -> None:
    assert (await _me(client, _conn(False)))["asistente_habilitado"] is False


async def test_me_sin_contexto_de_tenant_es_false(client: AsyncClient) -> None:
    assert (await _me(client, None))["asistente_habilitado"] is False


async def test_me_si_la_lectura_falla_es_false_y_no_rompe(client: AsyncClient) -> None:
    body = await _me(client, _conn(RuntimeError("registro caído")))
    assert body["asistente_habilitado"] is False
    assert "permissions" in body


async def test_me_lee_la_bandera_de_la_entidad_del_token(client: AsyncClient) -> None:
    """La consulta lleva el slug del token (no un literal): $1 = 'demo'."""
    conn = _conn(True)
    await _me(client, conn)
    llamadas = [c for c in conn.fetchval.call_args_list if "asistente_habilitado" in c.args[0]]
    assert [c.args[1] for c in llamadas] == ["demo"]
