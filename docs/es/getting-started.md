# Primeros Pasos / Getting Started

## Requisitos / Requirements

- Docker 24+ y Docker Compose v2
- 4 GB RAM mínimo (8 GB recomendado en producción)
- Puertos libres: 19080, 19180, 15432, 19900, 19901, 16379

> Los servicios internos siguen escuchando en 8001-8011/8080 dentro de la red Docker
> `orpycamcp-net`; lo que cambia son los puertos **publicados** al host (rango 19xxx /
> 15432 / 16379) para no chocar con otras pilas de desarrollo. Ver tabla completa en
> [Arquitectura](architecture.md).

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
| API Gateway (docs) | http://localhost:19080/docs | — |
| Frontend (SvelteKit) | http://localhost:19300 | — |
| Keycloak Admin | http://localhost:19180/admin | admin / orpycamcp_dev |
| MinIO Console | http://localhost:19901 | orpycamcp / orpycamcp_dev |
| PostgreSQL | localhost:15432 | orpycamcp / orpycamcp |
| MailHog (email dev) | http://localhost:8025 | — |

## Primer radicado / First Document Registration

```bash
# 1. Obtener token de acceso (vía el gateway, no directo al servicio)
TOKEN=$(curl -s -X POST 'http://localhost:19080/api/v1/auth/token' \
  -H 'Content-Type: application/json' \
  -d '{"username": "operador", "password": "orpycamcp_dev"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# 2. Radicar un documento de entrada
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

La respuesta incluye el número de radicado asignado (ej: `2024-DEMO-E-000001`).

## Agregar un tenant (institución) / Add a tenant (institution)

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

## Más ejemplos de uso end-to-end

Estos ejemplos asumen que ya tienes `$TOKEN` del paso anterior. El contrato completo
de cada endpoint está en [Referencia de API](api.md); el detalle de negocio detrás de
cada entidad está en [Dominio documental](domain.md).

### Crear un expediente y vincular un radicado

```bash
# 1. Crear el expediente (requiere TRD configurada para la serie)
curl -X POST 'http://localhost:19080/api/v1/expedientes/' \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"nombre": "Contrato 2024-001 — Suministro de papelería", "serie_id": "uuid-serie"}'
# Respuesta: { "id": "uuid-expediente", "estado": "open", ... }

# 2. Vincular el radicado creado antes
curl -X POST 'http://localhost:19080/api/v1/expedientes/uuid-expediente/radicados' \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"radicado_id": "uuid-radicado"}'

# 3. Cerrar el expediente (genera y firma el índice electrónico, ver Ciclo de vida)
curl -X PATCH 'http://localhost:19080/api/v1/expedientes/uuid-expediente' \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"estado": "closed"}'
```

### Firmar electrónicamente un radicado

```bash
curl -X POST 'http://localhost:19080/api/v1/signature/' \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"radicado_id": "uuid-radicado"}'
# Respuesta: { "firma_id": "uuid", "estado": "firmado", "sha256": "...", "firmado_at": "..." }
```

### Consultar al asistente conversacional

```bash
curl -X POST 'http://localhost:19080/api/v1/assistant/chat' \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"message": "¿Qué radicados de entrada tengo pendientes esta semana?"}'
# Respuesta: texto en lenguaje natural con citas a los radicados consultados (RAG, solo lectura)
```

Ver también: [Pantallas del sistema](pantallas.md) para la contraparte de cada uno de
estos ejemplos en la interfaz web.

<!-- TODO: translate to English -->
