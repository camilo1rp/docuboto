# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import uuid

import asyncpg

from app.repositories.tenant_repository import TenantRepository
from app.schemas.tenant import TenantCreate, TenantResponse, TenantUpdate


class DuplicateSlugError(Exception):
    pass


class DuplicateCodeError(Exception):
    pass


class TenantNotFoundError(Exception):
    pass


class TenantService:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._repo = TenantRepository(conn)

    async def create(self, payload: TenantCreate) -> TenantResponse:
        if await self._repo.exists_slug(payload.slug):
            raise DuplicateSlugError(payload.slug)
        if await self._repo.exists_code(payload.code):
            raise DuplicateCodeError(payload.code)

        tenant = await self._repo.create(
            slug=payload.slug,
            name=payload.name,
            code=payload.code,
            status=payload.status,
        )
        await self._repo.create_schema(payload.slug)

        return TenantResponse(
            id=tenant.id,
            slug=tenant.slug,
            name=tenant.name,
            code=tenant.code,
            status=tenant.status,
            created_at=tenant.created_at,
            updated_at=tenant.updated_at,
        )

    async def list(
        self,
        *,
        status: str | None,
        page: int,
        size: int,
    ) -> tuple[list[TenantResponse], int]:
        offset = (page - 1) * size
        tenants, total = await self._repo.list(status=status, limit=size, offset=offset)
        return [
            TenantResponse(
                id=t.id,
                slug=t.slug,
                name=t.name,
                code=t.code,
                status=t.status,
                created_at=t.created_at,
                updated_at=t.updated_at,
            )
            for t in tenants
        ], total

    async def get_by_id(self, tenant_id: uuid.UUID) -> TenantResponse:
        tenant = await self._repo.get_by_id(tenant_id)
        if tenant is None:
            raise TenantNotFoundError(str(tenant_id))
        return TenantResponse(
            id=tenant.id,
            slug=tenant.slug,
            name=tenant.name,
            code=tenant.code,
            status=tenant.status,
            created_at=tenant.created_at,
            updated_at=tenant.updated_at,
        )

    async def get_by_slug(self, slug: str) -> TenantResponse:
        tenant = await self._repo.get_by_slug(slug)
        if tenant is None:
            raise TenantNotFoundError(slug)
        return TenantResponse(
            id=tenant.id,
            slug=tenant.slug,
            name=tenant.name,
            code=tenant.code,
            status=tenant.status,
            created_at=tenant.created_at,
            updated_at=tenant.updated_at,
        )

    async def update(self, tenant_id: uuid.UUID, payload: TenantUpdate) -> TenantResponse:
        tenant = await self._repo.update(
            tenant_id,
            name=payload.name,
            status=payload.status,
        )
        if tenant is None:
            raise TenantNotFoundError(str(tenant_id))
        return TenantResponse(
            id=tenant.id,
            slug=tenant.slug,
            name=tenant.name,
            code=tenant.code,
            status=tenant.status,
            created_at=tenant.created_at,
            updated_at=tenant.updated_at,
        )
