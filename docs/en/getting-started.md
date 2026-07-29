# Getting Started

## Requirements

- Docker 24+ and Docker Compose v2
- 4 GB RAM minimum (8 GB recommended in production)
- Free ports: 19080, 19180, 15432, 19900, 19901, 16379

> Internal services keep listening on 8001-8011/8080 inside the `orpycamcp-net`
> Docker network; what changes is the port **published** to the host (19xxx /
> 15432 / 16379 range) to avoid clashing with other local dev stacks. See the full
> table in [Architecture](architecture.md#services-and-ports).

## Local Installation (development)

```bash
# 1. Clone the repository
git clone https://gitlab.com/orpyca/orpyca-mcp.git
cd orpyca-mcp

# 2. Start the full infrastructure
docker compose --profile dev up -d

# 3. Verify the services are running
docker compose ps
```

## Access URLs

| Service | URL | Dev credentials |
|---|---|---|
| API Gateway (docs) | http://localhost:19080/docs | — |
| Frontend (SvelteKit) | http://localhost:19300 | — |
| Keycloak Admin | http://localhost:19180/admin | admin / orpycamcp_dev |
| MinIO Console | http://localhost:19901 | orpycamcp / orpycamcp_dev |
| PostgreSQL | localhost:15432 | orpycamcp / orpycamcp |
| MailHog (email dev) | http://localhost:8025 | — |

## First Document Registration

A `radicado` is an official document registered with a unique, immutable tracking number.

```bash
# 1. Obtain an access token (via the gateway, not directly against the service)
TOKEN=$(curl -s -X POST 'http://localhost:19080/api/v1/auth/token' \
  -H 'Content-Type: application/json' \
  -d '{"username": "operador", "password": "orpycamcp_dev"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# 2. Register an incoming document
curl -X POST 'http://localhost:19080/api/v1/documents/' \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "subject": "Solicitud de información pública",
    "document_type": "entrada",
    "sender_name": "Ciudadano Juan Pérez",
    "sender_email": "juan@ejemplo.com"
  }'
```

The response includes the assigned `radicado` (tracking) number (e.g.: `2024-DEMO-E-000001`).

## Add a tenant (institution)

A `tenant` is an institution served by a shared OrpycaMCP installation.

```bash
curl -X POST 'http://localhost:19080/api/v1/tenants/' \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "slug": "mi_entidad",
    "name": "Mi Entidad Pública",
    "code": "MENT"
  }'
```

This automatically provisions the `tenant_mi_entidad` schema (each tenant's isolated database schema, named `tenant_{slug}`) in PostgreSQL.

## Load initial TRD (seed)

OrpycaMCP includes a reusable script to seed a tenant's TRD (`Tabla de Retención Documental` — document retention schedule) from a JSON file. The project ships the FondeCund TRD as a reference (150+ series).

**1. Export your institution's TRD to a compatible JSON:**

```json
{
  "entidad": "Mi Entidad Pública",
  "version": "2024-01-01",
  "dependencias": [
    {
      "nombre": "Departamento",
      "series": [
        {
          "codigo": 100,
          "nombre": "SERIE DOCUMENTAL",
          "retencion_gestion": 5,
          "retencion_central": 10,
          "disposicion_final": "Conservación total",
          "subseries": []
        }
      ]
    }
  ]
}
```

The Spanish final-disposition values are mapped to OrpycaMCP codes (`conserve`, `eliminate`, `transfer`).

**2. Generate the migration SQL for your tenant:**

```bash
python3 scripts/migrate_trd_from_fondecund.py \
  mi_entidad_trd.json \
  --tenant-slug mi_entidad \
  --output services/archive-service/migrations/003_trd_mi_entidad.sql
```

**3. Apply the migration in the tenant's schema:**

```bash
docker compose exec postgres psql -U orpycamcp -d orpycamcp_db \
  -c "SET search_path TO tenant_mi_entidad;" \
  -f services/archive-service/migrations/003_trd_mi_entidad.sql
```

## More end-to-end usage examples

These examples assume you already have `$TOKEN` from the previous step. The full
contract for each endpoint is in the [API Reference](api.md); the business detail
behind each entity is in [Document domain](domain.md).

### Create an expediente and link a radicado

```bash
# 1. Create the expediente (requires a TRD configured for the series)
curl -X POST 'http://localhost:19080/api/v1/expedientes/' \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"nombre": "Contract 2024-001 — Office supplies", "serie_id": "uuid-serie"}'
# Response: { "id": "uuid-expediente", "estado": "open", ... }

# 2. Link the radicado created earlier
curl -X POST 'http://localhost:19080/api/v1/expedientes/uuid-expediente/radicados' \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"radicado_id": "uuid-radicado"}'

# 3. Close the expediente (generates and signs the electronic index, see Lifecycle)
curl -X PATCH 'http://localhost:19080/api/v1/expedientes/uuid-expediente' \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"estado": "closed"}'
```

### Electronically sign a radicado

```bash
curl -X POST 'http://localhost:19080/api/v1/signature/' \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"radicado_id": "uuid-radicado"}'
# Response: { "firma_id": "uuid", "estado": "firmado", "sha256": "...", "firmado_at": "..." }
```

### Query the conversational assistant

```bash
curl -X POST 'http://localhost:19080/api/v1/assistant/chat' \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"message": "Which incoming radicados do I have pending this week?"}'
# Response: natural-language text with citations to the radicados consulted (RAG, read-only)
```

See also: [System screens](pantallas.md) for the web-UI counterpart of each of
these examples.
