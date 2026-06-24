# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import re
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

_SLUG_RE = re.compile(r"^[a-z0-9-]+$")
_CODE_RE = re.compile(r"^[A-Z0-9_]+$")

VALID_STATUSES = {"active", "inactive", "suspended"}


class TenantCreate(BaseModel):
    slug: str = Field(..., min_length=2, max_length=100)
    name: str = Field(..., min_length=2, max_length=500)
    code: str = Field(..., min_length=1, max_length=20)
    status: str = Field(default="active")

    @field_validator("slug")
    @classmethod
    def slug_must_be_lowercase_alphanumeric(cls, v: str) -> str:
        if not _SLUG_RE.match(v):
            raise ValueError("slug must contain only lowercase letters, digits, and hyphens")
        return v

    @field_validator("code")
    @classmethod
    def code_must_be_uppercase_alphanumeric(cls, v: str) -> str:
        upper = v.upper()
        if not _CODE_RE.match(upper):
            raise ValueError("code must contain only uppercase letters, digits, and underscores")
        return upper

    @field_validator("status")
    @classmethod
    def status_must_be_valid(cls, v: str) -> str:
        if v not in VALID_STATUSES:
            raise ValueError(f"status must be one of: {', '.join(sorted(VALID_STATUSES))}")
        return v


class TenantUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=500)
    status: str | None = None

    @field_validator("status")
    @classmethod
    def status_must_be_valid(cls, v: str | None) -> str | None:
        if v is not None and v not in VALID_STATUSES:
            raise ValueError(f"status must be one of: {', '.join(sorted(VALID_STATUSES))}")
        return v


class TenantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    slug: str
    name: str
    code: str
    status: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class TenantListResponse(BaseModel):
    items: list[TenantResponse]
    total: int


class HealthResponse(BaseModel):
    status: str
    database: str
