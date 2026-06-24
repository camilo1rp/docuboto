# Getting Started

## Requirements

- Docker 24+ and Docker Compose v2
- 4 GB RAM minimum (8 GB recommended in production)
- Free ports: 8080, 8180, 5432, 9000, 9001, 6379

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
| API Gateway (docs) | http://localhost:8080/docs | — |
| Keycloak Admin | http://localhost:8180/admin | admin / orpycamcp_dev |
| MinIO Console | http://localhost:9001 | orpycamcp / orpycamcp_dev |
| MailHog (email dev) | http://localhost:8025 | — |

## First Document Registration

A `radicado` is an official document registered with a unique, immutable tracking number.

```bash
# 1. Obtain an access token
TOKEN=$(curl -s -X POST 'http://localhost:8001/auth/token' \
  -H 'Content-Type: application/json' \
  -d '{"username": "operador", "password": "orpycamcp_dev"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# 2. Register an incoming document
curl -X POST 'http://localhost:8080/api/v1/documents/' \
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
curl -X POST 'http://localhost:8080/api/v1/tenants/' \
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
