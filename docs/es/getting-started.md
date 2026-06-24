# Primeros Pasos / Getting Started

## Requisitos / Requirements

- Docker 24+ y Docker Compose v2
- 4 GB RAM mínimo (8 GB recomendado en producción)
- Puertos libres: 8080, 8180, 5432, 9000, 9001, 6379

## Instalación local (desarrollo) / Local Installation (development)

```bash
# 1. Clonar el repositorio
git clone https://gitlab.com/orpyca/orpyca-mcp.git
cd orpyca-mcp

# 2. Levantar la infraestructura completa
docker compose --profile dev up -d

# 3. Verificar que los servicios estén corriendo
docker compose ps
```

## URLs de acceso / Access URLs

| Servicio / Service | URL | Credenciales dev |
|---|---|---|
| API Gateway (docs) | http://localhost:8080/docs | — |
| Keycloak Admin | http://localhost:8180/admin | admin / orpycamcp_dev |
| MinIO Console | http://localhost:9001 | orpycamcp / orpycamcp_dev |
| MailHog (email dev) | http://localhost:8025 | — |

## Primer radicado / First Document Registration

```bash
# 1. Obtener token de acceso
TOKEN=$(curl -s -X POST 'http://localhost:8001/auth/token' \
  -H 'Content-Type: application/json' \
  -d '{"username": "operador", "password": "orpycamcp_dev"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# 2. Radicar un documento de entrada
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

La respuesta incluye el número de radicado asignado (ej: `2024-DEMO-E-000001`).

## Agregar un tenant (institución) / Add a tenant (institution)

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

Esto aprovisiona automáticamente el schema `tenant_mi_entidad` en PostgreSQL.

## Cargar la TRD inicial (seed) / Load initial TRD (seed)

OrpycaMCP incluye un script reutilizable para sembrar la Tabla de Retención Documental (TRD) de un tenant a partir de un JSON. El proyecto trae como referencia la TRD de FondeCund (150+ series).

**1. Exportar la TRD de tu institución a un JSON compatible:**

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

Las disposiciones finales en español se mapean a los códigos de OrpycaMCP (`conserve`, `eliminate`, `transfer`).

**2. Generar el SQL de migración para tu tenant:**

```bash
python3 scripts/migrate_trd_from_fondecund.py \
  mi_entidad_trd.json \
  --tenant-slug mi_entidad \
  --output services/archive-service/migrations/003_trd_mi_entidad.sql
```

**3. Aplicar la migración en el schema del tenant:**

```bash
docker compose exec postgres psql -U orpycamcp -d orpycamcp_db \
  -c "SET search_path TO tenant_mi_entidad;" \
  -f services/archive-service/migrations/003_trd_mi_entidad.sql
```

<!-- TODO: translate to English -->
