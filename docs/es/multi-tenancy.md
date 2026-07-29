# Multi-tenancy / Multi-tenancy

OrpycaMCP soporta múltiples instituciones en una sola instalación usando **schema isolation** en PostgreSQL.

## ¿Qué hace?
Cada institución (tenant) tiene su propio schema de base de datos (`tenant_{slug}`), completamente aislado de los demás. El schema `public` solo contiene el registro de instituciones.

## ¿Por qué existe?
- Permite que una entidad gubernamental regional sirva a múltiples municipios
- Reduce costos operativos respecto a instalaciones separadas
- Garantiza aislamiento de datos (cada tenant no puede ver datos de otros)

## ¿Cómo agregar una institución?

```bash
# Con token de superadmin
curl -X POST 'http://localhost:19080/api/v1/tenants/' \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "slug": "gobernacion_caldas",
    "name": "Gobernación de Caldas",
    "code": "GCAL"
  }'
```

Esto:
1. Inserta en `public.tenants`
2. Crea el schema `tenant_gobernacion_caldas`
3. Ejecuta las migraciones Alembic en ese schema
4. Crea el bucket MinIO `orpycamcp-gobernacion_caldas-documents`

## ¿Qué puede salir mal?

- **Slug duplicado**: el sistema devuelve `409 Conflict`
- **Schema ya existe**: el provisioning es idempotente, no falla
- **MinIO no disponible**: la creación del tenant falla y se hace rollback de la BD

## ¿Cómo funciona el aislamiento en cada request?

El JWT del usuario incluye el claim `tenant_slug`. El middleware de FastAPI lo extrae y ejecuta:

```python
await session.execute(text(f"SET search_path TO tenant_{tenant_slug}, public"))
```

Todas las queries de esa sesión operan en el schema correcto automáticamente.

## Nomenclatura de recursos

| Recurso | Patrón | Ejemplo |
|---|---|---|
| Schema PostgreSQL | `tenant_{slug}` | `tenant_gobernacion_caldas` |
| Bucket MinIO documentos | `orpycamcp-{slug}-documents` | `orpycamcp-gobernacion_caldas-documents` |
| Bucket MinIO plantillas | `orpycamcp-{slug}-templates` | `orpycamcp-gobernacion_caldas-templates` |
| Stream Redis | `orpycamcp.{slug}.{service}.events` | `orpycamcp.gcal.document.events` |

<!-- TODO: translate to English -->
