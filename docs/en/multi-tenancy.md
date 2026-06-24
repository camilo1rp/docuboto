# Multi-tenancy

OrpycaMCP supports multiple institutions on a single installation using PostgreSQL **schema isolation**.

## What does it do?
Each institution (`tenant`) has its own database schema (`tenant_{slug}`), completely isolated from the others. The `public` schema holds only the institution registry.

## Why does it exist?
- Lets a regional government entity serve multiple municipalities
- Reduces operating costs compared to separate installations
- Guarantees data isolation (no tenant can see another's data)

## How do you add an institution?

```bash
# Con token de superadmin
curl -X POST 'http://localhost:8080/api/v1/tenants/' \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "slug": "gobernacion_caldas",
    "name": "Gobernación de Caldas",
    "code": "GCAL"
  }'
```

This:
1. Inserts a row into `public.tenants`
2. Creates the `tenant_gobernacion_caldas` schema
3. Runs the Alembic migrations in that schema
4. Creates the MinIO bucket `orpycamcp-gobernacion_caldas-documents`

## What can go wrong?

- **Duplicate slug**: the system returns `409 Conflict`
- **Schema already exists**: provisioning is idempotent, it does not fail
- **MinIO unavailable**: tenant creation fails and the database changes are rolled back

## How does isolation work on each request?

The user's JWT includes the `tenant_slug` claim. The FastAPI middleware extracts it and runs:

```python
await session.execute(text(f"SET search_path TO tenant_{tenant_slug}, public"))
```

All queries in that session automatically operate on the correct schema.

## Resource naming

| Resource | Pattern | Example |
|---|---|---|
| PostgreSQL schema | `tenant_{slug}` | `tenant_gobernacion_caldas` |
| MinIO documents bucket | `orpycamcp-{slug}-documents` | `orpycamcp-gobernacion_caldas-documents` |
| MinIO templates bucket | `orpycamcp-{slug}-templates` | `orpycamcp-gobernacion_caldas-templates` |
| Redis Stream | `orpycamcp.{slug}.{service}.events` | `orpycamcp.gcal.document.events` |
