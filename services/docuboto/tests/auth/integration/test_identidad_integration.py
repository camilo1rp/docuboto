# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""El emisor de identidad contra Postgres REAL (ADR-048).

Login con argon2id, bloqueo por intentos, rotación del refresh con detección de
reuso, historial de contraseñas, logout, y el recorrido completo por HTTP:
`POST /auth/login` → `GET /auth/me` con el Bearer emitido, verificado por
`UsuarioActualMiddleware` sin dobles.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator

import asyncpg
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from orpycamcp_common import usuario

from auth.core import passwords
from auth.services.auth_service import AuthError, AuthService
from docuboto.config import auth as settings
from tests.auth.app import app
from tests.auth.fakes_identidad import configurar
from tests.auth.integration.conftest import DSN, RUN_INTEGRATION

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.skipif(not RUN_INTEGRATION, reason="requiere Postgres real (RUN_INTEGRATION=1)"),
]

#: El de verdad, tomado al importar: el conftest de auth lo sustituye por uno
#: que acepta cualquier Bearer, y aquí se prueba justo la verificación.
_VALIDAR_REAL = usuario._validar

CLAVE = "una-clave-de-prueba-larga"


@pytest_asyncio.fixture
async def entidad(
    tenant_ctx: tuple[str, asyncpg.Connection], monkeypatch: pytest.MonkeyPatch
) -> AsyncGenerator[tuple[str, asyncpg.Connection, uuid.UUID], None]:
    """La entidad registrada en `public.tenants`, una persona con contraseña y una
    conexión propia en `public` para el servicio."""
    configurar(monkeypatch)
    slug, tconn = tenant_ctx
    await tconn.execute(
        "INSERT INTO public.tenants (slug, name, code) VALUES ($1, $2, $3)",
        slug,
        slug,
        slug.replace("-", "_").upper()[:20],
    )
    uid = await tconn.fetchval(
        "INSERT INTO auth_users (username, email, password_hash) VALUES ('OPERADOR', 'o@x.co', $1) "
        "RETURNING id",
        passwords.cifrar(CLAVE),
    )
    conn = await asyncpg.connect(dsn=DSN)
    try:
        yield slug, conn, uid
    finally:
        await conn.close()
        await tconn.execute("DELETE FROM public.tenants WHERE slug = $1", slug)


async def test_login_refresh_y_reuso(entidad: tuple[str, asyncpg.Connection, uuid.UUID]) -> None:
    slug, conn, uid = entidad
    svc = AuthService()
    t1 = await svc.login(conn, "operador", CLAVE, slug)
    v = await svc.validate(t1.access_token, conn)
    assert v.valid and v.user_id == str(uid) and v.tenant_slug == slug
    assert t1.refresh_token and t1.refresh_token.startswith(f"{slug}.")
    assert await conn.fetchval("SHOW search_path") == "public"

    t2 = await svc.refresh(conn, t1.refresh_token)
    assert t2.refresh_token != t1.refresh_token
    assert (await svc.validate(t2.access_token, conn)).user_id == str(uid)

    # Reusar el refresh ya gastado corta toda la familia, incluido el nuevo.
    with pytest.raises(AuthError) as e:
        await svc.refresh(conn, t1.refresh_token)
    assert e.value.code == "refresh_token_reused"
    with pytest.raises(AuthError):
        await svc.refresh(conn, t2.refresh_token or "")


async def test_el_refresh_no_alarga_la_sesion(
    entidad: tuple[str, asyncpg.Connection, uuid.UUID],
) -> None:
    slug, conn, _ = entidad
    svc = AuthService()
    t1 = await svc.login(conn, "OPERADOR", CLAVE, slug)
    t2 = await svc.refresh(conn, t1.refresh_token or "")
    schema = "tenant_" + slug.replace("-", "_")
    plazos = await conn.fetch(
        f'SELECT DISTINCT expires_at FROM "{schema}".auth_refresh_tokens'
    )
    assert len(plazos) == 1, "la rotación hereda el vencimiento de la familia"
    assert t2.refresh_token


async def test_bloqueo_por_intentos(
    entidad: tuple[str, asyncpg.Connection, uuid.UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, conn, _ = entidad
    monkeypatch.setattr(settings, "login_max_failed", 3)
    svc = AuthService()
    for _ in range(3):
        with pytest.raises(AuthError) as e:
            await svc.login(conn, "operador", "no-es-la-clave", slug)
        assert e.value.code == "invalid_credentials"
    with pytest.raises(AuthError) as e:
        await svc.login(conn, "operador", CLAVE, slug)
    assert e.value.status_code == 423 and e.value.code == "account_locked"


async def test_persona_inexistente_y_entidad_inexistente(
    entidad: tuple[str, asyncpg.Connection, uuid.UUID],
) -> None:
    slug, conn, _ = entidad
    svc = AuthService()
    with pytest.raises(AuthError) as e:
        await svc.login(conn, "nadie", CLAVE, slug)
    assert e.value.code == "invalid_credentials"
    with pytest.raises(AuthError) as e:
        await svc.login(conn, "operador", CLAVE, "no-existe-esta")
    assert e.value.code != "invalid_credentials" or e.value.status_code != 200


async def test_cambiar_la_clave_respeta_el_historial_y_cierra_sesiones(
    entidad: tuple[str, asyncpg.Connection, uuid.UUID],
) -> None:
    slug, conn, uid = entidad
    svc = AuthService()
    sesion = await svc.login(conn, "operador", CLAVE, slug)
    schema = "tenant_" + slug.replace("-", "_")
    await conn.execute(f'SET search_path = "{schema}"')
    try:
        await svc.set_password(conn, uid, "la-segunda-clave-larga")
        with pytest.raises(AuthError) as e:
            await svc.change_password(conn, uid, "la-segunda-clave-larga", "la-segunda-clave-larga")
        assert e.value.code == "password_reused"
        with pytest.raises(AuthError) as e:
            await svc.change_password(conn, uid, "mal", "una-tercera-clave-larga")
        assert e.value.code == "invalid_credentials"
    finally:
        await conn.execute("SET search_path = public")
    with pytest.raises(AuthError):
        await svc.refresh(conn, sesion.refresh_token or "")
    with pytest.raises(AuthError):
        await svc.login(conn, "operador", CLAVE, slug)
    assert await svc.login(conn, "operador", "la-segunda-clave-larga", slug)


async def test_logout_revoca(entidad: tuple[str, asyncpg.Connection, uuid.UUID]) -> None:
    slug, conn, _ = entidad
    svc = AuthService()
    t = await svc.login(conn, "operador", CLAVE, slug)
    await svc.logout(conn, t.refresh_token or "")
    await svc.logout(conn, "basura-sin-punto")  # idempotente, no lanza
    with pytest.raises(AuthError):
        await svc.refresh(conn, t.refresh_token or "")


async def test_recorrido_http_login_y_me(
    entidad: tuple[str, asyncpg.Connection, uuid.UUID], monkeypatch: pytest.MonkeyPatch
) -> None:
    slug, _, uid = entidad
    monkeypatch.setattr(usuario, "_validar", _VALIDAR_REAL)
    pool = await asyncpg.create_pool(dsn=DSN, min_size=1, max_size=3)
    app.state.db_pool = pool
    app.state.auth_service = AuthService()
    try:
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
            r = await ac.post(
                "/api/v1/auth/login",
                json={"username": "operador", "password": CLAVE, "tenant_slug": slug},
            )
            assert r.status_code == 200, r.text
            token = r.json()["access_token"]
            me = await ac.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
            assert me.status_code == 200, me.text
            assert me.json()["user_id"] == str(uid)
            assert me.json()["username"] == "OPERADOR"
            # Un token alterado no pasa del middleware.
            malo = await ac.get(
                "/api/v1/auth/me", headers={"Authorization": f"Bearer {token[:-4]}AAAA"}
            )
            assert malo.status_code == 401
            mal = await ac.post(
                "/api/v1/auth/login",
                json={"username": "operador", "password": "otra", "tenant_slug": slug},
            )
            assert mal.status_code == 401
            assert mal.json()["detail"]["error"] == "invalid_credentials"
            jwks = await ac.get("/.well-known/jwks.json")
            assert jwks.status_code == 200 and jwks.json()["keys"]
    finally:
        del app.state.db_pool
        del app.state.auth_service
        await pool.close()
