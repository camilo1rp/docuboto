# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

"""Schemas Pydantic v2 del RBAC (E08)."""

import uuid

from pydantic import BaseModel, Field


class UserCreate(BaseModel):
    username: str = Field(..., min_length=1, max_length=80)
    email: str | None = None
    keycloak_sub: str | None = None
    is_root: bool = False


class UserResponse(BaseModel):
    id: uuid.UUID
    username: str
    email: str | None
    keycloak_sub: str | None
    is_root: bool
    is_active: bool


class UserPickItem(BaseModel):
    """Proyección mínima de usuario para selectores de UI (D-10). Sin PII extra."""

    id: uuid.UUID
    username: str


class GroupCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    description: str | None = None


class GroupUpdate(BaseModel):
    """PATCH parcial de un grupo: solo se tocan los campos enviados (enviar
    `description: null` la borra)."""

    name: str | None = Field(None, min_length=1, max_length=120)
    description: str | None = None


class GroupResponse(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None


class PermissionResponse(BaseModel):
    id: uuid.UUID
    nombre: str
    descripcion: str | None


class GroupPermissionSet(BaseModel):
    crud: int = Field(..., ge=0, le=5)  # 0=Ninguno..5=Nivel_2


class GroupPermissionItem(BaseModel):
    """Permiso asignado a un grupo (lectura de `GET /groups/{group_id}/permissions`)."""

    permission_id: uuid.UUID
    nombre: str
    descripcion: str | None
    crud: int


class EffectivePermissions(BaseModel):
    user_id: uuid.UUID
    is_root: bool
    permissions: dict[str, int]  # nombre → MAX(crud)
