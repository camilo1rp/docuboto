# Multi-tenancy

OrpycaMCP supports multiple institutions on a single installation using PostgreSQL **schema isolation**.

## What does it do?
Each institution (`tenant`) has its own database schema (`tenant_{slug}`), completely isolated from the others. The `public` schema holds only the institution registry.

## Why does it exist?
- Lets a regional government entity serve multiple municipalities
- Reduces operating costs compared to separate installations
- Guarantees data isolation (no tenant can see another's data)

## How do you add an institution?

An installation serves **one institution** (ADR-047 §3); the schema per institution stays because it
costs one statement per connection and keeps the option of hosting several. Onboarding is a **platform
operation**, not an application one: there's no HTTP route for it. It is done by `scripts/init_tenant.py` (which
`scripts/init-tenant.sh` runs in a container, with the database owner role):

```bash
./scripts/init-tenant.sh gobernacion-caldas "Gobernación de Caldas" GCAL
```

This:
1. Validates the slug (`^[a-z0-9-]+$`: lowercase, digits and **hyphen**, no underscore) and registers it in `public.tenants`
2. Creates the `tenant_gobernacion_caldas` schema (the hyphen becomes an underscore) and the application role's `GRANT USAGE`
3. Applies the migration chain in `services/docuboto/migrations/` (`global/` to `public`, `tenant/` to the new schema) and records it in the `public.schema_migrations` ledger (ADR-053)
The app runs with the application role and **executes no DDL**. The buckets
(`orpycamcp-{slug}-documents`, and the preservation ones) are created by the `storage` package the
first time it needs them; ADR-050 retires the object store.

## What can go wrong?

- **Duplicate slug**: the system returns `409 Conflict`
- **Invalid slug** (uppercase, underscore, leading hyphen, fewer than 2 or more than 54 characters; the same validator as every service, `orpycamcp_common.tenant`): `422`; the schema is named `tenant_{slug}` and PostgreSQL silently truncates identifiers to 63 bytes
- **Schema already exists**: provisioning is idempotent, it does not fail
- **Failed migration**: without its schema the institution answers `503 tenant_no_provisionado` until provisioning finishes

## How does isolation work on each request?

`UsuarioActualMiddleware` validates the JWT once per request and stores the slug (`tenant_slug` claim)
in `request.state`; an `X-Tenant-Slug` sent by the client is dropped. The
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
| Event stream (`stream` column of the `eventos` table in the institution's schema) | `orpycamcp.{service}.events` | `orpycamcp.document.events` |

The stream **does not carry the slug**: there is one per service, and since ADR-049 events live in the
institution's schema; the signed envelope (ADR-010) still carries the institution, which the consumer checks.

## Entity state

There are no lifecycle states (ADR-047 §3): `public.tenants` is the institution's configuration row.
It's served when its row and its schema exist; without the row you get `403 tenant_inactivo`, and
without the schema `503 tenant_no_provisionado`. `get_tenant_conn` and `get_vetted_conn`
(`orpycamcp_common.database`) check this before handing out the connection. Public routes answer the
same `404` for a missing institution or schema. There's no suspended state and no preservation
exception any more.
