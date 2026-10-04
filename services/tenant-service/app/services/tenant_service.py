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

from app.core import audit as audit_core
from app.repositories.tenant_repository import TenantRepository
from app.schemas.tenant import TenantCreate, TenantResponse, TenantUpdate


class DuplicateSlugError(Exception):
    pass


class DuplicateCodeError(Exception):
    pass


class TenantNotFoundError(Exception):
    pass


# Los actos sobre el registro de entidades son de plataforma: no pertenecen al
# tenant afectado (su `audit_log` lo ve la propia entidad) sino al sistema.
_SYSTEM_TENANT = "__system__"
_ACTOR_SERVICIO = "service:tenant-internal-token"


class TenantService:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn
        self._repo = TenantRepository(conn)

    async def create(
        self, payload: TenantCreate, *, actor: uuid.UUID | None = None
    ) -> TenantResponse:
        """Registra la entidad. NO crea el esquema ni sus tablas.

        El esquema `tenant_{slug}` lo provisiona `scripts/init_tenant.py` (rol
        propietario + migraciones de los 11 servicios). Este servicio opera con
        el rol de aplicación, que no puede ejecutar DDL: por eso la fila nace
        con `provisioning_status='pending'` y la provisión la pasa a `active`.
        Alta y asiento van en la MISMA transacción (ADR-010).
        """
        audit_core.require_audit_available()
        async with self._conn.transaction():
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
            await audit_core.append(
                self._conn,
                tenant_slug=_SYSTEM_TENANT,
                action="tenant.created",
                object_type="tenant",
                object_ref=tenant.slug,
                actor=actor or _ACTOR_SERVICIO,
                canal="system",
                payload={
                    "despues": {
                        "name": tenant.name,
                        "code": tenant.code,
                        "status": tenant.status,
                    },
                    "provisioning_status": "pending",
                },
            )

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

    async def update(
        self,
        tenant_id: uuid.UUID,
        payload: TenantUpdate,
        *,
        actor: uuid.UUID | None = None,
    ) -> TenantResponse:
        audit_core.require_audit_available()
        async with self._conn.transaction():
            before = await self._repo.get_for_update(tenant_id)
            if before is None:
                raise TenantNotFoundError(str(tenant_id))
            tenant = await self._repo.update(
                tenant_id,
                name=payload.name,
                status=payload.status,
            )
            if tenant is None:
                raise TenantNotFoundError(str(tenant_id))
            await audit_core.append(
                self._conn,
                tenant_slug=_SYSTEM_TENANT,
                action="tenant.updated",
                object_type="tenant",
                object_ref=tenant.slug,
                actor=actor or _ACTOR_SERVICIO,
                canal="system",
                payload={
                    "antes": {"name": before.name, "status": before.status},
                    "despues": {"name": tenant.name, "status": tenant.status},
                },
            )
        return TenantResponse(
            id=tenant.id,
            slug=tenant.slug,
            name=tenant.name,
            code=tenant.code,
            status=tenant.status,
            created_at=tenant.created_at,
            updated_at=tenant.updated_at,
        )
