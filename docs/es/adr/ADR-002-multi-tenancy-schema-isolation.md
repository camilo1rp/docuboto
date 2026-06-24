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

```python
# On each request, extract tenant from JWT and switch search_path
async def set_tenant_schema(session: AsyncSession, tenant_slug: str):
    await session.execute(
        text(f"SET search_path TO tenant_{tenant_slug}, public")
    )
```

### MinIO Bucket Pattern

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
- Alembic migrations must run per tenant schema
- `search_path` must be set at session start — handled by middleware

## Alternatives Considered

**Row-level isolation** (tenant_id column): Simpler schema management, but risk of data leaks if WHERE clause is forgotten. Not suitable for government data.

**Separate databases per tenant**: Strongest isolation, but exponentially higher operational cost for large numbers of tenants.
