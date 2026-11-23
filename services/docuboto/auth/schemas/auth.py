# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class LoginRequest(BaseModel):
    username: str = Field(..., min_length=1, max_length=150)
    password: str = Field(..., min_length=1, max_length=1024)
    tenant_slug: str = Field(..., min_length=1, max_length=100)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1, max_length=512)


class LogoutRequest(BaseModel):
    refresh_token: str = Field(..., min_length=1, max_length=512)


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=1024)
    new_password: str = Field(..., min_length=1, max_length=1024)


class TokenResponse(BaseModel):
    access_token: str
    #: `None` en el token de una cuenta de servicio: no hay sesión que renovar.
    refresh_token: str | None = None
    token_type: str = "Bearer"
    expires_in: int


class ValidateResponse(BaseModel):
    """Lo que `UsuarioActualMiddleware` necesita de un access token ya verificado."""

    valid: bool
    user_id: str = ""
    username: str = ""
    tenant_slug: str | None = None
    #: Segundos que le quedan al token (`exp - ahora`, >= 0).
    expires_in: int | None = None


class UserInfoResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: str
    username: str
    email: str
    tenant_slug: str | None
    permissions: list[str] = []
    """Effective permission names for the authenticated user (D-07 / RBAC mirror).

    Resolved from the tenant DB on each call; empty when the user has no
    tenant context or is not provisioned in the tenant (fail-closed).
    Root users always include ``USUA_PERM_ROOT``. Kept as a flat name list for
    backwards compatibility with existing consumers (frontend store, other
    services) — see ``permission_levels`` for the CRUD level of each.
    """
    asistente_habilitado: bool = False
    """¿La entidad del token tiene activada la capa de conocimiento? (ADR-037)

    Se lee de ``public.tenants.asistente_habilitado`` (el nombre de columna se
    conserva). Es para MOSTRAR u ocultar (UI, catálogo del MCP): knowledge-service
    vuelve a leerlo de la base en cada consulta. Fail-closed: ``False`` sin
    contexto de tenant o si el registro no responde.
    """
    intelligence_provider: Literal["local", "ppi"] = "local"
    """Proveedor de inteligencia de la entidad (ADR-045). ``local`` si no se pudo leer.

    La UI lo usa para decidir qué mostrar. knowledge-service vuelve a leer la
    columna en cada envío.
    """
    permission_levels: dict[str, int] = Field(default_factory=dict)
    """CRUD level (0-5) per permission name — same keys as ``permissions``, added
    so consumers can distinguish read from write access (RF-SEG-03) instead of
    treating any listed permission as full access.

    Values come straight from ``auth_group_permissions.crud``
    (``services/docuboto/migrations/tenant/020_auth_groups.sql``): 0=Ninguno 1=Leer 2=Editar
    3=Crear/Borrar 4=Nivel_1 5=Nivel_2. Root users get ``USUA_PERM_ROOT: 5``.

    Fail-closed like ``permissions``: empty dict (never omitted/null) when
    there is no tenant context or the user is not provisioned.
    """
