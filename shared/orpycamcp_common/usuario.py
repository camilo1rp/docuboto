# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Un llamante por petición, tomado del JWT. Las cabeceras `X-User-Id` no cuentan.

El gateway firmaba quién eras y lo inyectaba. En un solo proceso esa firma es un
salto de más: el middleware valida el Bearer, deja un `Usuario` en
`request.state` y veta el estado de la entidad. Un `X-User-Id` de cliente se
ignora. Permiso y clearance siguen saliendo de la BD.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from urllib.parse import unquote

from starlette.requests import Request

from orpycamcp_common.client_ip import resolve_client_ip
from orpycamcp_common.tenant import veredicto_entidad

logger = logging.getLogger(__name__)

_autorizacion: ContextVar[str | None] = ContextVar("autorizacion_actual", default=None)

_PUBLICAS = frozenset(
    {
        "/health",
        "/health/ready",
        "/metrics",
        "/api/v1/auth/token",
        "/api/v1/auth/refresh",
    }
)
_RUTAS_OPENAPI = frozenset(
    {
        "/api/v1/openapi.json",
        "/api/v1/docs",
        "/api/v1/redoc",
        "/docs",
        "/redoc",
        "/openapi.json",
    }
)
_DOT = {".", ".."}
_PROHIBIDOS = frozenset({"?", "#", "\x00"})


@dataclass(frozen=True)
class Usuario:
    """Identidad ya validada de esta petición. `id` es `auth_users.id`."""

    id: uuid.UUID | None
    tenant_slug: str
    username: str = ""
    roles: tuple[str, ...] = ()
    canal: str = "api"
    ip: str = ""


def usuario_de_peticion(request: Request) -> Usuario | None:
    """`Usuario` que el middleware dejó en `request.state`, o `None`."""
    return getattr(request.state, "usuario", None)


def id_opcional(request: Request) -> uuid.UUID | None:
    """UUID del llamante si hay usuario; si no, `None` (lecturas fail-closed)."""
    u = usuario_de_peticion(request)
    return None if u is None else u.id


def exigir_id_o_400(request: Request) -> uuid.UUID:
    """UUID del llamante; 400 con el cuerpo histórico si esta petición no tiene persona."""
    from fastapi import HTTPException, status

    uid = id_opcional(request)
    if uid is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "missing_user",
                "detail": "Falta la cabecera X-User-Id",
                "status": 400,
            },
        )
    return uid


def id_exigido(request: Request) -> uuid.UUID:
    """UUID del llamante; 401 si esta petición no tiene persona."""
    from fastapi import HTTPException, status

    u = usuario_de_peticion(request)
    if u is None or u.id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "unauthorized",
                "detail": "Falta la identidad del llamante",
                "status": 401,
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
    return u.id


def slug_de(request: Request) -> str:
    """Slug del JWT si hay persona; si no, `X-Tenant-Slug` de una llamada interna."""
    u = usuario_de_peticion(request)
    if u is not None and u.tenant_slug:
        return u.tenant_slug
    return request.headers.get("X-Tenant-Slug", "") or ""


def canal_de(request: Request) -> str:
    """Canal de auditoría. Lo decide el middleware, no el cliente."""
    u = usuario_de_peticion(request)
    if u is not None:
        return u.canal
    return "system"


def ip_de(request: Request) -> str | None:
    """IP derivada por saltos de confianza, o `None` si no hay una útil."""
    ip = getattr(request.state, "ip", None)
    if isinstance(ip, str) and ip and ip != "unknown":
        return ip
    u = usuario_de_peticion(request)
    if u is not None and u.ip and u.ip != "unknown":
        return u.ip
    return None


def autorizacion_actual() -> str | None:
    """Bearer de esta petición, para un salto HTTP interno al mismo proceso."""
    return _autorizacion.get()


def cabeceras_internas(*, tenant_slug: str, token: str | None = None) -> dict[str, str]:
    """Slug del tenant, el Bearer del llamante si hay, y el token interno."""
    headers = {"X-Tenant-Slug": tenant_slug}
    auth = autorizacion_actual()
    if auth:
        headers["Authorization"] = auth
    if token:
        headers["X-Internal-Token"] = token
    return headers


async def usuario_actual(request: Request) -> Usuario:
    """Dependencia FastAPI: la persona de esta petición, o 401."""
    from fastapi import HTTPException, status

    u = usuario_de_peticion(request)
    if u is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "unauthorized",
                "detail": "Falta la identidad del llamante",
                "status": 401,
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
    return u


def _es_publica(path: str, *, openapi_publico: bool) -> bool:
    if path in _PUBLICAS:
        return True
    if path.startswith("/api/v1/public/") or path.startswith("/ppi/"):
        return True
    if path in _RUTAS_OPENAPI or path.startswith("/docs/"):
        return openapi_publico
    return False


def _ruta_invalida(path: str) -> bool:
    if any(seg in _DOT for seg in path.split("/")):
        return True
    return any(c in unquote(path) for c in _PROHIBIDOS)


def _es_interna(path: str) -> bool:
    return any(p == "internal" or p.endswith("-internal") for p in path.split("/"))


async def _responder(
    send: Any,
    status: int,
    error: str,
    detail: str,
    extra: tuple[tuple[bytes, bytes], ...] = (),
) -> None:
    cuerpo = json.dumps({"error": error, "detail": detail, "status": status}).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(cuerpo)).encode()),
                *extra,
            ],
        }
    )
    await send({"type": "http.response.body", "body": cuerpo})


def _canal(path: str, origen: str | None, tenant: str, authorization: str) -> str:
    if path.startswith("/api/v1/mcp/"):
        return "mcp"
    try:
        from orpycamcp_common import identidad

        if identidad.verificar_origen(origen, tenant=tenant, authorization=authorization) == "mcp":
            return "mcp"
    except Exception:  # noqa: BLE001 — sin secreto de origen el canal es api
        pass
    return "api"


def _uuid(valor: object) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(valor))
    except (ValueError, TypeError, AttributeError):
        return None


class UsuarioActualMiddleware:
    """Valida el Bearer, deja `Usuario` en el state, ignora `X-User-Id`.

    Sin Bearer no corta: las rutas internas van con `X-Internal-Token` y las
    públicas no traen persona. Quien exige identidad lo hace en la dependencia.
    """

    def __init__(self, app):  # noqa: ANN001 — firma ASGI
        self.app = app

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if scope["type"] == "lifespan":
            await self.app(scope, receive, send)
            return
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path") or ""
        if _ruta_invalida(path):
            await _responder(
                send,
                400,
                "invalid_path",
                "La ruta no puede contener segmentos '.' ni '..' ni los "
                "caracteres '?', '#' o nulos codificados.",
            )
            return

        cabeceras = {
            k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])
        }
        state = scope.setdefault("state", {})
        app = scope.get("app")
        trusted = ""
        if app is not None:
            trusted = str(getattr(app.state, "trusted_proxies", "") or "")
        if not trusted:
            trusted = os.environ.get("TRUSTED_PROXIES", "")
        try:
            state["ip"] = resolve_client_ip(Request(scope, receive), trusted)
        except Exception:  # noqa: BLE001
            state["ip"] = "unknown"

        token_interno = cabeceras.get("x-internal-token", "")
        if _es_interna(path) and not token_interno:
            await _responder(
                send,
                404,
                "route_not_found",
                "Ninguna ruta del sistema corresponde a la solicitud",
            )
            return

        if state.get("usuario") is not None:
            await self.app(scope, receive, send)
            return

        openapi_publico = bool(getattr(getattr(app, "state", None), "openapi_publico", False))
        if _es_publica(path, openapi_publico=openapi_publico):
            await self.app(scope, receive, send)
            return

        auth = cabeceras.get("authorization") or ""
        if not auth.lower().startswith("bearer "):
            await self.app(scope, receive, send)
            return

        token = auth.split(" ", 1)[1].strip()
        payload = await _validar(scope, token)
        if payload is None:
            await _responder(
                send,
                401,
                "unauthorized",
                "El token es inválido o expiró",
                extra=((b"www-authenticate", b"Bearer"),),
            )
            return
        if not payload.get("valid"):
            await _responder(
                send,
                401,
                "unauthorized",
                "El token es inválido o expiró",
                extra=((b"www-authenticate", b"Bearer"),),
            )
            return

        slug = str(payload.get("tenant_slug") or "")
        if slug:
            veto = await _veto(scope, slug)
            if veto is not None:
                await _responder(send, veto[0], veto[1], veto[2])
                return

        ip = str(state.get("ip") or "unknown")

        roles = payload.get("roles") or []
        if not isinstance(roles, list):
            roles = []
        usuario = Usuario(
            id=_uuid(payload.get("user_id")),
            tenant_slug=slug,
            username=str(payload.get("username") or ""),
            roles=tuple(str(r) for r in roles),
            canal=_canal(
                path,
                cabeceras.get("x-origen-canal"),
                slug,
                auth,
            ),
            ip=ip,
        )
        state["usuario"] = usuario
        token_var = _autorizacion.set(auth)
        try:
            await self.app(scope, receive, send)
        finally:
            _autorizacion.reset(token_var)


class InyectarUsuarioDePrueba:
    """Tests: copia `X-User-Id` / `X-Tenant-Slug` al state. Producción no lo monta."""

    def __init__(self, app):  # noqa: ANN001
        self.app = app

    async def __call__(self, scope, receive, send):  # noqa: ANN001
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        state = scope.setdefault("state", {})
        if state.get("usuario") is None:
            cab = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope.get("headers", [])}
            uid = _uuid(cab.get("x-user-id"))
            slug = cab.get("x-tenant-slug") or ""
            if uid is not None or slug:
                state["usuario"] = Usuario(
                    id=uid,
                    tenant_slug=slug,
                    canal=cab.get("x-canal") or "api",
                    ip=cab.get("x-real-ip") or "",
                )
        auth = ""
        for k, v in scope.get("headers", []):
            if k.decode("latin-1").lower() == "authorization":
                auth = v.decode("latin-1")
                break
        token_var = _autorizacion.set(auth or None)
        try:
            await self.app(scope, receive, send)
        finally:
            _autorizacion.reset(token_var)


def instalar_inyector(app: Any) -> None:
    """Monta el inyector de tests. Llamar desde `conftest` después de crear `app`."""
    app.add_middleware(InyectarUsuarioDePrueba)


async def _validar(scope: dict[str, Any], token: str) -> dict[str, Any] | None:
    app = scope.get("app")
    if app is None:
        return None
    auth_service = getattr(app.state, "auth_service", None)
    pool = getattr(app.state, "db_pool", None)
    if auth_service is not None and pool is not None:
        try:
            async with pool.acquire() as conn:
                result = await auth_service.validate(token, conn)
        except Exception:
            logger.exception("validate in-process falló")
            return None
        if hasattr(result, "model_dump"):
            return result.model_dump()
        if isinstance(result, dict):
            return result
        return {
            "valid": bool(getattr(result, "valid", False)),
            "user_id": getattr(result, "user_id", None),
            "username": getattr(result, "username", ""),
            "tenant_slug": getattr(result, "tenant_slug", None),
            "roles": getattr(result, "roles", []) or [],
            "tenant_estado": getattr(result, "tenant_estado", None),
        }

    url = os.environ.get("AUTH_SERVICE_URL")
    if not url:
        return None
    try:
        import httpx

        async with httpx.AsyncClient(base_url=url, timeout=5.0) as client:
            resp = await client.get(
                "/api/v1/auth/validate",
                headers={"Authorization": f"Bearer {token}"},
            )
    except Exception:
        logger.exception("validate HTTP falló")
        return None
    if resp.status_code != 200:
        return None
    data = resp.json()
    return data if isinstance(data, dict) else None


async def _veto(scope: dict[str, Any], slug: str) -> tuple[int, str, str] | None:
    app = scope.get("app")
    pool = getattr(getattr(app, "state", None), "db_pool", None)
    if pool is None:
        return None
    try:
        async with pool.acquire() as conn:
            return await veredicto_entidad(conn, slug)
    except Exception:
        logger.exception("veredicto_entidad falló")
        return (
            503,
            "tenant_estado_no_disponible",
            "No se pudo comprobar el estado de la entidad.",
        )
