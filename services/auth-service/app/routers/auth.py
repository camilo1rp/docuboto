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
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from orpycamcp_common.tenant import (
    ERROR_ESTADO_NO_DISPONIBLE,
    ERRORES_ENTIDAD,
    asistente_habilitado,
)

from app.core.database import get_db_conn, get_optional_tenant_conn
from app.repositories.audit_repository import AuditRepository
from app.repositories.rbac_repository import RBACRepository
from app.schemas.auth import (
    LogoutRequest,
    RefreshRequest,
    TokenRequest,
    TokenResponse,
    UserInfoResponse,
    ValidateResponse,
)
from app.services.auth_service import AuthService, KeycloakError
from app.services.rbac_service import RBACService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

_auth_service: AuthService | None = None


def get_auth_service() -> AuthService:
    if _auth_service is None:
        raise RuntimeError("AuthService not initialised — check app lifespan")
    return _auth_service


def set_auth_service(service: AuthService) -> None:
    global _auth_service
    _auth_service = service


bearer_scheme = HTTPBearer(auto_error=False)


def _client_ip(request: Request) -> str | None:
    """IP que se sella en el `audit_log`, derivada SERVER-SIDE por el gateway.

    Antes se tomaba el primer valor de `X-Forwarded-For`, una cabecera que
    escribe el propio cliente y que el gateway reenvía tal cual: la IP de los
    asientos de login/logout —parte de la cadena de custodia, inmutable— la
    elegía quien se estaba autenticando. Un campo probatorio falsificable con una
    cabecera es peor que uno vacío, porque el vacío no engaña a nadie.

    `X-Real-Ip` la calcula el gateway por saltos de confianza y NO está en su
    lista de cabeceras reenviadas desde el cliente (mismo patrón que `X-Canal`),
    así que no se puede forjar desde fuera.

    Sin esa cabecera —llamada directa al servicio, sin pasar por el gateway— se
    usa la dirección del socket. El `X-Forwarded-For` crudo NO se consulta: si el
    gateway no la derivó, esa cabecera no es más fiable aquí que allí.
    """
    real_ip = request.headers.get("X-Real-Ip")
    if real_ip:
        return real_ip.strip()
    if request.client:
        return request.client.host
    return None


def _keycloak_error_to_http(exc: KeycloakError) -> HTTPException:
    error_map = {
        401: ("invalid_credentials", "Invalid username or password"),
        403: ("tenant_mismatch", "User does not belong to the specified tenant"),
    }
    error_code, detail = error_map.get(exc.status_code, ("keycloak_error", exc.detail))
    # ADR-035: la entidad no operativa se responde con el MISMO código que el gateway.
    for estado_http, estado_codigo, estado_detalle in (
        *ERRORES_ENTIDAD.values(),
        ERROR_ESTADO_NO_DISPONIBLE,
    ):
        if exc.status_code == estado_http and exc.detail == estado_codigo:
            error_code, detail = estado_codigo, estado_detalle
    return HTTPException(
        status_code=exc.status_code,
        detail={"error": error_code, "detail": detail, "status": exc.status_code},
    )


@router.post(
    "/token",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Obtain access and refresh tokens",
)
async def token(
    payload: TokenRequest,
    request: Request,
    service: AuthService = Depends(get_auth_service),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TokenResponse:
    audit = AuditRepository(conn)
    try:
        result = await service.login(
            payload.username, payload.password, payload.tenant_slug, conn=conn
        )
        await audit.log(
            "login_success",
            username=payload.username,
            tenant_slug=payload.tenant_slug,
            ip_address=_client_ip(request),
            user_agent=request.headers.get("User-Agent"),
        )
        return result
    except KeycloakError as exc:
        await audit.log(
            "login_failure",
            username=payload.username,
            tenant_slug=payload.tenant_slug,
            ip_address=_client_ip(request),
            detail=exc.detail,
        )
        raise _keycloak_error_to_http(exc)


@router.post(
    "/refresh",
    response_model=TokenResponse,
    status_code=status.HTTP_200_OK,
    summary="Refresh an access token",
)
async def refresh_token(
    payload: RefreshRequest,
    request: Request,
    service: AuthService = Depends(get_auth_service),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> TokenResponse:
    audit = AuditRepository(conn)
    try:
        result = await service.refresh(payload.refresh_token, conn=conn)
        await audit.log("token_refresh", ip_address=_client_ip(request))
        return result
    except KeycloakError as exc:
        raise _keycloak_error_to_http(exc)


@router.post(
    "/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Invalidate a refresh token (logout)",
)
async def logout(
    payload: LogoutRequest,
    request: Request,
    service: AuthService = Depends(get_auth_service),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> None:
    audit = AuditRepository(conn)
    try:
        await service.logout(payload.refresh_token)
        await audit.log("logout", ip_address=_client_ip(request))
    except KeycloakError as exc:
        raise _keycloak_error_to_http(exc)


@router.get(
    "/validate",
    response_model=ValidateResponse,
    status_code=status.HTTP_200_OK,
    summary="Validate a Bearer token — called by api-gateway",
)
async def validate(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    service: AuthService = Depends(get_auth_service),
    conn: asyncpg.Connection = Depends(get_db_conn),
) -> ValidateResponse:
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "missing_token",
                "detail": "Falta la cabecera Authorization",
                "status": 401,
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

    audit = AuditRepository(conn)
    # Pass the connection so validate() can resolve the internal auth_users.id via
    # lazy provisioning (uses the conn's search_path after switching to tenant schema).
    result = await service.validate(credentials.credentials, conn)

    # Solo se asienta el RECHAZO, no la validación exitosa.
    #
    # El gateway llama a este endpoint en TODA petición no pública, así que
    # `token_valid` producía **una fila por request** en `public.audit_log` —
    # un registro con valor probatorio, encadenado por hash y con
    # `pg_advisory_xact_lock` por tenant en cada escritura. Dos daños distintos:
    # sepultaba los eventos que de verdad importan (login, firma, reclasificación,
    # disposición) bajo ruido de varios órdenes de magnitud, haciendo la traza
    # ilegible justo para quien viene a investigar; y serializaba todas las
    # peticiones del tenant sobre ese lock.
    #
    # Un token válido presentado por su portador no es un acto auditable: el acto
    # es lo que hace DESPUÉS con él, y eso ya lo audita cada servicio. Un token
    # inválido sí lo es —es un intento de acceso fallido— y se conserva.
    if not result.valid:
        await audit.log(
            "token_invalid",
            username=result.username or None,
            tenant_slug=result.tenant_slug,
            ip_address=_client_ip(request),
        )

    return result


@router.get(
    "/me",
    response_model=UserInfoResponse,
    status_code=status.HTTP_200_OK,
    summary="Return current user info from Bearer token, including effective permissions",
)
async def me(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    service: AuthService = Depends(get_auth_service),
    conn: asyncpg.Connection | None = Depends(get_optional_tenant_conn),
) -> UserInfoResponse:
    """Identidad + permisos efectivos del portador del token.

    ``permissions`` es la lista plana de nombres de permiso (compatibilidad con
    consumidores existentes: store del frontend, otros servicios). Cada nombre
    tiene un nivel CRUD asociado en `auth_group_permissions.crud` que esta lista
    NO expone — un permiso en nivel Leer y el mismo en nivel Crear/Borrar son
    indistinguibles en ``permissions``, aunque los backends gatean por nivel
    mínimo (``require_permission(name, min_crud=...)``, RF-SEG-03). Por eso se
    añade ``permission_levels: dict[str, int]`` (mismas claves, nivel 0-5):
    0=Ninguno 1=Leer 2=Editar 3=Crear/Borrar 4=Nivel_1 5=Nivel_2
    (``migrations/tenant/001_rbac.sql``). ROOT resuelve ``USUA_PERM_ROOT: 5``.

    Fail-closed en ambos campos: sin contexto de tenant o con usuario no
    aprovisionado, ambos quedan vacíos (nunca ausentes ni null) — un consumidor
    que interprete "ausente" como "sin restricción" abriría una fuga.
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={
                "error": "missing_token",
                "detail": "Falta la cabecera Authorization",
                "status": 401,
            },
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        user_info = await service.get_current_user(credentials.credentials)
    except KeycloakError as exc:
        raise _keycloak_error_to_http(exc)

    # Resolve effective permissions and the internal auth_users.id from the tenant DB.
    # `user_info.user_id` is the raw Keycloak sub from the JWT claims.
    # Fail-closed: empty permissions and sub as user_id when:
    #   - no tenant context (superadmin / no X-Tenant-Slug header), or
    #   - user is not yet provisioned in the tenant.
    permissions: list[str] = []
    permission_levels: dict[str, int] = {}
    if conn is not None:
        repo = RBACRepository(conn)
        user = await repo.get_user_by_keycloak_sub(user_info.user_id)
        if user is not None:
            # Replace keycloak sub with the internal id for coherence with X-User-Id.
            user_info = user_info.model_copy(update={"user_id": str(user["id"])})
            rbac = RBACService(conn)
            # resolve_effective_permissions embeds USUA_PERM_ROOT for root users
            # (consistent with rbac.py:88 and has_permission() logic).
            perms = await rbac.resolve_effective_permissions(user["id"])
            permissions = list(perms.keys())
            permission_levels = perms

    # ADR-037: la bandera por entidad viaja con la identidad (UI y mcp-server no
    # consultan tenant-service). Falla cerrado: sin tenant o con el registro caído,
    # apagado — `/me` no se rompe por esto.
    asistente = False
    if conn is not None and user_info.tenant_slug:
        try:
            asistente = await asistente_habilitado(conn, user_info.tenant_slug)
        except Exception:  # noqa: BLE001 — no poder saberlo se responde como «apagado»
            logger.warning("no se pudo leer asistente_habilitado en /me", exc_info=True)

    return user_info.model_copy(
        update={
            "permissions": permissions,
            "permission_levels": permission_levels,
            "asistente_habilitado": asistente,
        }
    )
