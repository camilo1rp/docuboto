# Multi-tenancy

OrpycaMCP supports multiple institutions on a single installation using PostgreSQL **schema isolation**.

## What does it do?
Each institution (`tenant`) has its own database schema (`tenant_{slug}`), completely isolated from the others. The `public` schema holds only the institution registry.

## Why does it exist?
- Lets a regional government entity serve multiple municipalities
- Reduces operating costs compared to separate installations
- Guarantees data isolation (no tenant can see another's data)

## How do you add an institution?

Onboarding is a **platform operation**, not an application one: `/api/v1/tenants` is **deliberately not
exposed by the api-gateway** (any authenticated user of one institution could enumerate and write
the others' registry; critical finding, 2026-07). It is done by `scripts/init_tenant.py` (which
`scripts/init-tenant.sh` runs in a container, with the database owner role):

```bash
./scripts/init-tenant.sh gobernacion-caldas "Gobernación de Caldas" GCAL
```

This:
1. Validates the slug (`^[a-z0-9-]+$`: lowercase, digits and **hyphen**, no underscore) and registers it in `public.tenants`
2. Creates the `tenant_gobernacion_caldas` schema (the hyphen becomes an underscore) and the application role's `GRANT USAGE`
3. Applies each service's numbered `.sql` migrations (`migrations/global/` and `migrations/tenant/`) through `migrations/runner.py` and the single `public.schema_migrations` ledger
4. Moves the institution from `provisioning_status='pending'` to `active`

tenant-service's `POST /tenants` (internal, no gateway route) only registers the `pending` row and writes
its `audit_log` entry: the service runs with the application role and **executes no DDL**. It does not
touch the object store either: the buckets (`orpycamcp-{slug}-documents`, and the preservation ones)
are created by storage-service the first time it needs them.

## What can go wrong?

- **Duplicate slug**: the system returns `409 Conflict`
- **Invalid slug** (uppercase, underscore, leading hyphen, fewer than 2 or more than 54 characters; the same validator as every service, `orpycamcp_common.tenant`): `422`; the schema is named `tenant_{slug}` and PostgreSQL silently truncates identifiers to 63 bytes
- **Schema already exists**: provisioning is idempotent, it does not fail
- **Failed migration**: the institution stays `pending` and serves no requests until provisioning finishes

## How does isolation work on each request?

The gateway validates the JWT (`tenant_slug` claim) and forwards the tenant to the service. The
`get_tenant_conn` dependency **validates the slug with `orpycamcp_common.tenant` (one validator: `^[a-z0-9][a-z0-9-]{1,53}$`, fail-closed, `400 invalid_tenant`),
turns it into a schema name and double-quotes it** before setting the `search_path` on an `asyncpg`
connection; a schema name cannot be a `$1` parameter, so the validation is what prevents injection:

```python
schema = tenant_schema(slug)  # ^[a-z0-9][a-z0-9-]{1,53}$ -> "tenant_" + slug.replace("-", "_")
async with pool.acquire() as conn:
    await conn.execute(f'SET search_path = "{schema}", public')
```

Every query on that connection operates on the correct schema. **Never** interpolate an unvalidated
or unquoted slug into a `SET search_path`.

## Resource naming

| Resource | Pattern | Example |
|---|---|---|
| PostgreSQL schema | `tenant_{slug, hyphen → underscore}` | `tenant_gobernacion_caldas` |
| Documents bucket | `orpycamcp-{slug}-documents` | `orpycamcp-gobernacion-caldas-documents` |
| Preservation (WORM) bucket | `orpycamcp-{slug}-preservation` | `orpycamcp-gobernacion-caldas-preservation` |
| Redis Stream (shared by all institutions) | `orpycamcp.{service}.events` | `orpycamcp.document.events` |

The stream **does not carry the slug**: there is one per service, and the institution travels inside the
event's signed envelope (ADR-010).
