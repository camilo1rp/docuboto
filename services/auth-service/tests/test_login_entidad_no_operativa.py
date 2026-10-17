# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Una entidad que no se atiende no recibe token (B9-N4, ADR-035).

El gateway ya veta cada petición con el `tenant_estado` de `/auth/validate`, pero el
login y el refresh entregaban igualmente un token: la persona entraba, pasaba la
pantalla de sesión y solo entonces chocaba con un 403, o veía un error genérico.
Ahora el login responde el MISMO código que el gateway y NO entrega el token. Se mira
la entidad que el token LLEVA en su claim (la que el gateway verá), no la del cuerpo.
Ejerce `AuthService.login/refresh` reales con Keycloak simulado a nivel HTTP.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest
from httpx import AsyncClient

from app.services.auth_service import AuthService, KeycloakError


class _Conn:
    def __init__(self, fila: dict | None, error: Exception | None = None) -> None:
        self.fila, self.error = fila, error

    async def fetchrow(self, sql: str, *args: object) -> dict | None:
        if self.error:
            raise self.error
        return self.fila if "public.tenants" in sql else None


def _fila(status: str = "active", prov: str = "active", schema: bool = True) -> dict:
    return {"status": status, "provisioning_status": prov, "hay_schema": schema}


def _servicio() -> AuthService:
    def _keycloak(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"access_token": "AT", "refresh_token": "RT", "expires_in": 300},
        )

    svc = AuthService()
    svc._client = httpx.AsyncClient(transport=httpx.MockTransport(_keycloak))
    return svc


def _claims(tenant: str | None = "demo", roles: list[str] | None = None) -> AsyncMock:
    return AsyncMock(return_value={"tenant_slug": tenant, "roles": roles or ["operator"]})


@pytest.mark.parametrize(
    ("fila", "http", "error"),
    [
        (_fila(status="suspended"), 403, "tenant_inactivo"),
        (_fila(status="inactive"), 403, "tenant_inactivo"),
        (None, 403, "tenant_inactivo"),
        (_fila(prov="pending"), 503, "tenant_no_provisionado"),
        (_fila(schema=False), 503, "tenant_no_provisionado"),
    ],
    ids=["suspendida", "inactiva", "inexistente", "pendiente", "sin_schema"],
)
async def test_el_login_de_una_entidad_no_operativa_no_entrega_token(
    fila: dict | None, http: int, error: str
) -> None:
    with patch("app.services.auth_service.validate_token", _claims("demo")):
        with pytest.raises(KeycloakError) as exc:
            await _servicio().login("operador", "x", "demo", conn=_Conn(fila))  # type: ignore[arg-type]
    assert (exc.value.status_code, exc.value.detail) == (http, error)


async def test_el_refresh_de_una_entidad_suspendida_tampoco_entrega_token() -> None:
    with patch("app.services.auth_service.validate_token", _claims("demo")):
        with pytest.raises(KeycloakError) as exc:
            await _servicio().refresh("RT", conn=_Conn(_fila(status="suspended")))  # type: ignore[arg-type]
    assert (exc.value.status_code, exc.value.detail) == (403, "tenant_inactivo")


async def test_si_el_registro_no_responde_no_se_entrega_token() -> None:
    with patch("app.services.auth_service.validate_token", _claims("demo")):
        with pytest.raises(KeycloakError) as exc:
            await _servicio().login(
                "operador", "x", "demo", conn=_Conn(None, RuntimeError("caído"))  # type: ignore[arg-type]
            )
    assert (exc.value.status_code, exc.value.detail) == (503, "tenant_estado_no_disponible")


async def test_una_entidad_operativa_recibe_su_token() -> None:
    with patch("app.services.auth_service.validate_token", _claims("demo")):
        out = await _servicio().login("operador", "x", "demo", conn=_Conn(_fila()))  # type: ignore[arg-type]
    assert out.access_token == "AT"


async def test_un_superadmin_sin_entidad_en_el_token_recibe_token() -> None:
    """Sin entidad no hay nada que comprobar: es quien puede reactivar a las demás."""
    with patch("app.services.auth_service.validate_token", _claims(None, ["superadmin"])):
        out = await _servicio().login("root", "x", "demo", conn=_Conn(None))  # type: ignore[arg-type]
    assert out.access_token == "AT"


async def test_se_mira_la_entidad_del_token_no_la_del_cuerpo() -> None:
    """El cuerpo dice 'demo' (activa) pero el token lleva 'otra' (suspendida): se niega."""
    conn_por_slug = {"demo": _fila(), "otra": _fila(status="suspended")}
    visto: list[str] = []

    class _ConnPorSlug(_Conn):
        async def fetchrow(self, sql: str, *args: object) -> dict | None:
            visto.append(str(args[0]))
            return conn_por_slug.get(str(args[0]))

    with patch("app.services.auth_service.validate_token", _claims("otra", ["superadmin"])):
        with pytest.raises(KeycloakError) as exc:
            await _servicio().login("root", "x", "demo", conn=_ConnPorSlug(None))  # type: ignore[arg-type]
    assert exc.value.detail == "tenant_inactivo"
    assert visto == ["otra"]


async def test_el_endpoint_responde_el_mismo_codigo_que_el_gateway(client: AsyncClient) -> None:
    with patch(
        "app.services.auth_service.AuthService.login",
        new_callable=AsyncMock,
        side_effect=KeycloakError(403, "tenant_inactivo"),
    ):
        r403 = await client.post(
            "/api/v1/auth/token",
            json={"username": "operador", "password": "x", "tenant_slug": "demo"},
        )
    with patch(
        "app.services.auth_service.AuthService.login",
        new_callable=AsyncMock,
        side_effect=KeycloakError(503, "tenant_no_provisionado"),
    ):
        r503 = await client.post(
            "/api/v1/auth/token",
            json={"username": "operador", "password": "x", "tenant_slug": "demo"},
        )

    assert r403.status_code == 403
    assert r403.json()["detail"] == {
        "error": "tenant_inactivo",
        "detail": "Esta entidad no está activa en la plataforma.",
        "status": 403,
    }
    assert r503.status_code == 503
    assert r503.json()["detail"]["error"] == "tenant_no_provisionado"
    assert "access_token" not in r403.text + r503.text


async def test_tenant_mismatch_sigue_siendo_tenant_mismatch(client: AsyncClient) -> None:
    """Regresión del mapeo: el 403 de otra causa no se confunde con la entidad."""
    with patch(
        "app.services.auth_service.AuthService.login",
        new_callable=AsyncMock,
        side_effect=KeycloakError(403, "tenant_mismatch"),
    ):
        resp = await client.post(
            "/api/v1/auth/token",
            json={"username": "operador", "password": "x", "tenant_slug": "otra"},
        )
    assert resp.json()["detail"]["error"] == "tenant_mismatch"


# ---- Re-auditoria B9 G: el rechazo revoca la sesion recien emitida en Keycloak ----


def _servicio_que_registra(logout_status: int = 204) -> tuple[AuthService, list[httpx.Request]]:
    """Keycloak simulado que anota cada llamada a la revocacion (logout) del realm."""
    from app.core.config import settings

    revocaciones: list[httpx.Request] = []

    def _keycloak(request: httpx.Request) -> httpx.Response:
        if str(request.url) == settings.keycloak_logout_url:
            revocaciones.append(request)
            return httpx.Response(logout_status)
        return httpx.Response(
            200,
            json={"access_token": "AT", "refresh_token": "RT-NUEVO", "expires_in": 300},
        )

    svc = AuthService()
    svc._client = httpx.AsyncClient(transport=httpx.MockTransport(_keycloak))
    return svc, revocaciones


async def test_el_login_rechazado_por_entidad_REVOCA_el_refresh_token_emitido() -> None:
    svc, revocaciones = _servicio_que_registra()
    with patch("app.services.auth_service.validate_token", _claims("demo")):
        with pytest.raises(KeycloakError) as exc:
            await svc.login("operador", "x", "demo", conn=_Conn(_fila(status="suspended")))  # type: ignore[arg-type]
    assert exc.value.detail == "tenant_inactivo"
    assert len(revocaciones) == 1
    assert b"refresh_token=RT-NUEVO" in revocaciones[0].content


async def test_el_refresh_rechazado_por_entidad_REVOCA_el_nuevo_refresh_token() -> None:
    svc, revocaciones = _servicio_que_registra()
    with patch("app.services.auth_service.validate_token", _claims("demo")):
        with pytest.raises(KeycloakError):
            await svc.refresh("RT", conn=_Conn(_fila(status="suspended")))  # type: ignore[arg-type]
    assert len(revocaciones) == 1
    assert b"refresh_token=RT-NUEVO" in revocaciones[0].content


async def test_una_entidad_operativa_NO_revoca_nada() -> None:
    """Control positivo: sin el, una revocacion incondicional pasaria los tests de arriba."""
    svc, revocaciones = _servicio_que_registra()
    with patch("app.services.auth_service.validate_token", _claims("demo")):
        out = await svc.login("operador", "x", "demo", conn=_Conn(_fila()))  # type: ignore[arg-type]
    assert out.refresh_token == "RT-NUEVO"
    assert revocaciones == []


async def test_si_la_revocacion_falla_el_rechazo_sigue_siendo_el_de_la_entidad() -> None:
    svc, revocaciones = _servicio_que_registra(logout_status=500)
    with patch("app.services.auth_service.validate_token", _claims("demo")):
        with pytest.raises(KeycloakError) as exc:
            await svc.login("operador", "x", "demo", conn=_Conn(_fila(status="suspended")))  # type: ignore[arg-type]
    assert (exc.value.status_code, exc.value.detail) == (403, "tenant_inactivo")
    assert len(revocaciones) == 1
