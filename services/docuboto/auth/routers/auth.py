# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import logging

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request, status
from orpycamcp_common.database import get_conn as get_db_conn
from orpycamcp_common.database import get_optional_tenant_conn, get_tenant_conn
from orpycamcp_common.tenant import asistente_habilitado, intelligence_provider
from orpycamcp_common.usuario import Usuario, ip_de, usuario_actual

from auth.repositories.audit_repository import AuditRepository
from auth.repositories.rbac_repository import RBACRepository
from auth.schemas.auth import (
    LoginRequest,
    LogoutRequest,
    PasswordChangeRequest,
    RefreshRequest,
    TokenResponse,
    UserInfoResponse,
)
from auth.services.auth_service import AuthError, AuthService
from auth.services.rbac_service import RBACService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_auth_service = AuthService()


def get_auth_service() -> AuthService:
    return _auth_service


def _client_ip(request: Request) -> str | None:
    """IP que se sella en el `audit_log`, derivada SERVER-SIDE.

    La resuelve `UsuarioActualMiddleware` caminando `X-Forwarded-For` desde el
    final por saltos de confianza (`TRUSTED_PROXIES`). Un `X-Forwarded-For` o un
    `X-Real-Ip` crudos los escribe el cliente: un campo probatorio falsificable
    es peor que uno vacío. Sin IP derivada se usa la dirección del socket.
    """
    real_ip = ip_de(request)
    if real_ip:
        return real_ip
    if request.client:
        return request.client.host
    return None


def auth_error_to_http(exc: AuthError) -> HTTPException:
    headers = {"WWW-Authenticate": "Bearer"} if exc.status_code == 401 else None
    return HTTPException(
        status_code=exc.status_code,
        detail={"error": exc.code, "detail": exc.detail, "status": exc.status_code},
        headers=headers,
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Inicia sesión con usuario y contraseña",
)
async def login(
    payload: LoginRequest,
    request: Request,
    service: AuthService = Depends(get_auth_service),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TokenResponse:
    audit = AuditRepository(conn)
    try:
        result = await service.login(conn, payload.username, payload.password, payload.tenant_slug)
    except AuthError as exc:
        await audit.log(
            "login_failure",
            username=payload.username,
            tenant_slug=payload.tenant_slug,
            ip_address=_client_ip(request),
            detail=exc.code,
        )
        raise auth_error_to_http(exc) from None
    await audit.log(
        "login_success",
        username=payload.username,
        tenant_slug=payload.tenant_slug,
        ip_address=_client_ip(request),
        user_agent=request.headers.get("User-Agent"),
    )
    return result


@router.post(
    "/refresh",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Rota el refresh token y entrega un access token nuevo",
)
async def refresh_token(
    payload: RefreshRequest,
    request: Request,
    service: AuthService = Depends(get_auth_service),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TokenResponse:
    try:
        result = await service.refresh(conn, payload.refresh_token)
    except AuthError as exc:
        if exc.code == "refresh_token_reused":
            await AuditRepository(conn).log(
                "refresh_token_reused", ip_address=_client_ip(request)
            )
        raise auth_error_to_http(exc) from None
    await AuditRepository(conn).log("token_refresh", ip_address=_client_ip(request))
    return result


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoca la sesión del refresh token",
)
async def logout(
    payload: LogoutRequest,
    request: Request,
    service: AuthService = Depends(get_auth_service),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> None:
    await service.logout(conn, payload.refresh_token)
    await AuditRepository(conn).log("logout", ip_address=_client_ip(request))


@router.post(
    "/password",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="La persona cambia su contraseña (cierra sus demás sesiones)",
)
async def change_password(
    payload: PasswordChangeRequest,
    usuario: Usuario = Depends(usuario_actual),
    service: AuthService = Depends(get_auth_service),
    conn: asyncpg.Connection = Depends(get_tenant_conn),
) -> None:
    if usuario.id is None:
        raise auth_error_to_http(AuthError(401, "unauthorized"))
    try:
        await service.change_password(
            conn, usuario.id, payload.current_password, payload.new_password
        )
    except AuthError as exc:
        raise auth_error_to_http(exc) from None


@router.get(
    "/me",
    response_model=UserInfoResponse,
    status_code=status.HTTP_200_OK,
    summary="Identidad del portador del token y sus permisos efectivos",
)
async def me(
    usuario: Usuario = Depends(usuario_actual),
    conn: asyncpg.Connection | None = Depends(get_optional_tenant_conn),
) -> UserInfoResponse:
    """Identidad + permisos efectivos del portador del token.

    ``permissions`` es la lista plana de nombres de permiso; ``permission_levels``
    da el nivel CRUD de cada uno (0-5):
    0=Ninguno 1=Leer 2=Editar 3=Crear/Borrar 4=Nivel_1 5=Nivel_2
    (``services/docuboto/migrations/tenant/020_auth_groups.sql``). ROOT resuelve ``USUA_PERM_ROOT: 5``.

    Fail-closed en ambos campos: sin contexto de tenant o con usuario no
    encontrado, ambos quedan vacíos (nunca ausentes ni null) — un consumidor
    que interprete "ausente" como "sin restricción" abriría una fuga.
    """
    user_info = UserInfoResponse(
        user_id=str(usuario.id or ""),
        username=usuario.username,
        email="",
        tenant_slug=usuario.tenant_slug or None,
    )
    permissions: list[str] = []
    permission_levels: dict[str, int] = {}
    if conn is not None and usuario.id is not None:
        user = await RBACRepository(conn).get_user(usuario.id)
        if user is not None and user["is_active"]:
            user_info = user_info.model_copy(
                update={"username": user["username"], "email": user["email"] or ""}
            )
            perms = await RBACService(conn).resolve_effective_permissions(user["id"])
            permissions = list(perms.keys())
            permission_levels = perms

    # ADR-037: la bandera por entidad viaja con la identidad. Falla cerrado.
    asistente = False
    proveedor = "local"
    if conn is not None and user_info.tenant_slug:
        try:
            asistente = await asistente_habilitado(conn, user_info.tenant_slug)
        except Exception:  # noqa: BLE001 — no poder saberlo se responde como «apagado»
            logger.warning("no se pudo leer asistente_habilitado en /me", exc_info=True)
        try:
            proveedor = await intelligence_provider(conn, user_info.tenant_slug)
        except Exception:  # noqa: BLE001 — no poder saberlo se responde como «local»
            logger.warning("no se pudo leer intelligence_provider en /me", exc_info=True)

    return user_info.model_copy(
        update={
            "permissions": permissions,
            "permission_levels": permission_levels,
            "asistente_habilitado": asistente,
            "intelligence_provider": proveedor,
        }
    )
