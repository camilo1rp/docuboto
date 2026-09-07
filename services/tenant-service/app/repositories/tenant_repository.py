# OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
# Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
# Contacto: aurigadl@gmail.com
#
# Software libre: este archivo se distribuye bajo la Licencia Pública General
# Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
# conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
# SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import uuid
from datetime import UTC, datetime

import asyncpg

from app.models.tenant import Tenant


class TenantRepository:
    def __init__(self, conn: asyncpg.Connection) -> None:
        self._conn = conn

    async def create(self, slug: str, name: str, code: str, status: str) -> Tenant:
        row = await self._conn.fetchrow(
            """
            INSERT INTO public.tenants (slug, name, code, status)
            VALUES ($1, $2, $3, $4)
            RETURNING id, slug, name, code, status, created_at, updated_at
            """,
            slug,
            name,
            code,
            status,
        )
        return _row_to_tenant(row)

    async def create_schema(self, slug: str) -> None:
        # Schema name is built from a validated slug (lowercase alnum + hyphens).
        # asyncpg does not support parameterized DDL identifiers, so we interpolate
        # directly after validation has already rejected unsafe characters.
        schema_name = f"tenant_{slug.replace('-', '_')}"
        await self._conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"')

        # El rol de la aplicación necesita USAR el esquema nuevo.
        #
        # Los privilegios por omisión (migración 008) cubren las TABLAS que se
        # creen dentro, pero no el permiso de uso del esquema en sí: sin esta
        # línea, la entidad se da de alta sin ruido y falla en su primera
        # consulta, ya en producción. Se comprobó ejecutándolo — conceder sobre
        # lo existente no alcanza a lo que aún no existe.
        #
        # Va aquí, en el mismo acto que crea el esquema, y no como paso aparte
        # del despliegue: un permiso que depende de que alguien se acuerde es el
        # que se olvida.
        if await self._existe_rol_app():
            await self._conn.execute(
                f'GRANT USAGE ON SCHEMA "{schema_name}" TO orpycamcp_app'
            )

    async def _existe_rol_app(self) -> bool:
        """¿Existe el rol de aplicación? No todos los despliegues lo han activado.

        Conceder a un rol inexistente aborta el alta de la entidad, y el reparto
        de roles es opcional: un despliegue que aún conecta con el propietario
        debe poder seguir dando de alta entidades.
        """
        return bool(
            await self._conn.fetchval(
                "SELECT 1 FROM pg_roles WHERE rolname = 'orpycamcp_app'"
            )
        )

    async def get_by_id(self, tenant_id: uuid.UUID) -> Tenant | None:
        row = await self._conn.fetchrow(
            """
            SELECT id, slug, name, code, status, created_at, updated_at
            FROM public.tenants
            WHERE id = $1
            """,
            tenant_id,
        )
        return _row_to_tenant(row) if row else None

    async def get_by_slug(self, slug: str) -> Tenant | None:
        row = await self._conn.fetchrow(
            """
            SELECT id, slug, name, code, status, created_at, updated_at
            FROM public.tenants
            WHERE slug = $1
            """,
            slug,
        )
        return _row_to_tenant(row) if row else None

    async def exists_slug(self, slug: str) -> bool:
        val = await self._conn.fetchval("SELECT 1 FROM public.tenants WHERE slug = $1", slug)
        return val is not None

    async def exists_code(self, code: str) -> bool:
        val = await self._conn.fetchval("SELECT 1 FROM public.tenants WHERE code = $1", code)
        return val is not None

    async def list(
        self,
        *,
        status: str | None,
        limit: int,
        offset: int,
    ) -> tuple[list[Tenant], int]:
        if status is not None:
            rows = await self._conn.fetch(
                """
                SELECT id, slug, name, code, status, created_at, updated_at
                FROM public.tenants
                WHERE status = $1
                ORDER BY created_at DESC
                LIMIT $2 OFFSET $3
                """,
                status,
                limit,
                offset,
            )
            total: int = await self._conn.fetchval(
                "SELECT COUNT(*) FROM public.tenants WHERE status = $1", status
            )
        else:
            rows = await self._conn.fetch(
                """
                SELECT id, slug, name, code, status, created_at, updated_at
                FROM public.tenants
                ORDER BY created_at DESC
                LIMIT $1 OFFSET $2
                """,
                limit,
                offset,
            )
            total = await self._conn.fetchval("SELECT COUNT(*) FROM public.tenants")

        return [_row_to_tenant(r) for r in rows], total

    async def update(
        self,
        tenant_id: uuid.UUID,
        *,
        name: str | None,
        status: str | None,
    ) -> Tenant | None:
        fields: list[str] = []
        params: list[object] = []
        idx = 1

        if name is not None:
            fields.append(f"name = ${idx}")
            params.append(name)
            idx += 1

        if status is not None:
            fields.append(f"status = ${idx}")
            params.append(status)
            idx += 1

        if not fields:
            return await self.get_by_id(tenant_id)

        fields.append(f"updated_at = ${idx}")
        params.append(datetime.now(tz=UTC))
        idx += 1

        params.append(tenant_id)
        set_clause = ", ".join(fields)

        row = await self._conn.fetchrow(
            f"""
            UPDATE public.tenants
            SET {set_clause}
            WHERE id = ${idx}
            RETURNING id, slug, name, code, status, created_at, updated_at
            """,
            *params,
        )
        return _row_to_tenant(row) if row else None


def _row_to_tenant(row: asyncpg.Record) -> Tenant:
    return Tenant(
        id=row["id"],
        slug=row["slug"],
        name=row["name"],
        code=row["code"],
        status=row["status"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )
