# ADR-002: Multi-tenancy via PostgreSQL Schema Isolation

**Date**: 2026-05-28
**Status**: Accepted
**Deciders**: Giampiero (orpyca.com)

## Context

OrpycaMCP must support multiple institutions (tenants) in a single installation. We need to decide how to isolate tenant data.

## Decision

Use **PostgreSQL schema isolation**: each tenant gets its own schema (`tenant_{slug}`), while the `public` schema holds only the tenant registry.

```sql
-- public schema
CREATE TABLE public.tenants (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    slug VARCHAR(50) UNIQUE NOT NULL,  -- e.g., 'icetex', 'bog_limpia'
    name VARCHAR(200) NOT NULL,
    is_active BOOLEAN DEFAULT true,
    created_at TIMESTAMPTZ DEFAULT now()
);

-- Per-tenant schema (provisioned on tenant creation)
CREATE SCHEMA tenant_icetex;
-- All domain tables created within this schema
```

### Session Middleware Pattern

> **Nota (2026-10-03) — parcialmente superado por [ADR-003](ADR-003-asyncpg-sin-orm.md) (sin ORM ni Alembic) y [ADR-027](ADR-027-sustitucion-almacen-objetos-rustfs.md) (RustFS).**
> La decisión (un schema por tenant) sigue vigente; la receta original interpolaba el slug sin
> validar ni entrecomillar, que es una inyección en `search_path`. La forma real es la de abajo.

```python
# On each request: validate the slug (fail-closed), derive the schema, quote it
schema = tenant_schema(request.headers.get("X-Tenant-Slug", ""))  # ^[a-z0-9-]+$ -> tenant_{slug}
async with pool.acquire() as conn:                                  # asyncpg, no ORM
    await conn.execute(f'SET search_path = "{schema}"')   # SOLO el tenant (addendum C-7)
```

### Object-store Bucket Pattern (originally "MinIO"; now RustFS, ADR-027)

```
orpycamcp-{slug}-documents     # Annexes and files
orpycamcp-{slug}-templates     # Document templates
```

## Consequences

**Positive:**
- Strong data isolation between tenants
- Each tenant schema can be backed up/restored independently
- Queries don't need a `tenant_id` column on every table
- Supports tenant-specific migrations

**Negative:**
- Schema provisioning required on tenant creation (automated via tenant-service)
- Numbered `.sql` migrations (applied by `migrations/runner.py`; originally written as "Alembic", superseded by ADR-003) must run per tenant schema
- `search_path` must be set at session start — handled by middleware

## Alternatives Considered

**Row-level isolation** (tenant_id column): Simpler schema management, but risk of data leaks if WHERE clause is forgotten. Not suitable for government data.

**Separate databases per tenant**: Strongest isolation, but exponentially higher operational cost for large numbers of tenants.

## Addendum (2026-10-22, C-7 / SEG #12): the `search_path` is only the tenant

The path used to be `"tenant_x", public`. A missing tenant table then resolved **silently** against its namesake in `public` (14 tenant tables had in fact landed there, with data, because a tenant migration ran with a path whose first schema did not exist: `CREATE TABLE` falls into the first schema that does). Now:

- `SET search_path = "{schema}"` — nothing else. A missing table is `UndefinedTable`; a runner against a missing schema fails with `3F000` instead of creating a table in `public`. `SET search_path = public` alone (a connection without a tenant) stays.
- **Everything global is qualified `public.`** (`public.audit_log`, `public.tenants`, `public.schema_migrations`, `public.divipola_*`). Extensions live in `public` and are named qualified: `$1::public.vector`, `OPERATOR(public.<=>)`, `public.gin_trgm_ops`, `public.vector_cosine_ops`, `public.unaccent`, `'public.spanish_unaccent'::regconfig`; `CREATE EXTENSION ... WITH SCHEMA public`.
- The 14 orphan tenant tables were dropped from `public` (tenant-service global migration 005); the functions whose `proconfig` named `public` were re-fixed (tenant-service tenant 016).
- A static guardian (`shared/tests/test_el_search_path_es_solo_el_tenant.py`) and a real-Postgres check enforce it. Rejected: a dedicated `extensions` schema (needs superuser `ALTER EXTENSION` on every deploy) and keeping `, public` while emptying `public` (no verifiable invariant). See [ADR-044](ADR-044-identidad-de-un-solo-uso-y-search-path-sin-public.md).
