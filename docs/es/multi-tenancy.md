# Multi-tenancy / Multi-tenancy

OrpycaMCP soporta múltiples instituciones en una sola instalación usando **schema isolation** en PostgreSQL.

## ¿Qué hace?
Cada institución (tenant) tiene su propio schema de base de datos (`tenant_{slug}`), completamente aislado de los demás. El schema `public` solo contiene el registro de instituciones.

## ¿Por qué existe?
- Permite que una entidad gubernamental regional sirva a múltiples municipios
- Reduce costos operativos respecto a instalaciones separadas
- Garantiza aislamiento de datos (cada tenant no puede ver datos de otros)

## ¿Cómo agregar una institución?

El alta es una **operación de plataforma**, no de aplicación: `/api/v1/tenants` **no se expone por el
api-gateway, a propósito** (cualquier usuario autenticado de una entidad podría enumerar y escribir
el registro de las demás; hallazgo crítico 2026-07). La hace `scripts/init_tenant.py` (que
`scripts/init-tenant.sh` ejecuta en un contenedor, con el rol propietario de la base):

```bash
./scripts/init-tenant.sh gobernacion-caldas "Gobernación de Caldas" GCAL
```

Esto:
1. Valida el slug (`^[a-z0-9-]+$`: minúsculas, dígitos y **guion**, sin guion bajo) y lo registra en `public.tenants`
2. Crea el schema `tenant_gobernacion_caldas` (el guion pasa a guion bajo) y el `GRANT USAGE` del rol de aplicación
3. Aplica las migraciones `.sql` numeradas de cada servicio (`migrations/global/` y `migrations/tenant/`), con el ejecutor `migrations/runner.py` y el libro único `public.schema_migrations`
4. Pasa la entidad de `provisioning_status='pending'` a `active`

`POST /tenants` del tenant-service (interno, sin ruta en el gateway) solo registra la fila `pending` y deja
su asiento en `audit_log`: el servicio opera con el rol de aplicación y **no ejecuta DDL**. Tampoco toca el
almacén de objetos: los buckets (`orpycamcp-{slug}-documents`, y los de preservación) los crea el
storage-service la primera vez que los necesita.

## ¿Qué puede salir mal?

- **Slug duplicado**: el sistema devuelve `409 Conflict`
- **Slug no válido** (mayúsculas, guion bajo, guion inicial, menos de 2 o más de 54 caracteres; el mismo validador que todos los servicios, `orpycamcp_common.tenant`): `422`; el schema se llama `tenant_{slug}` y PostgreSQL trunca los identificadores a 63 bytes sin avisar
- **Schema ya existe**: el provisioning es idempotente, no falla
- **Migración fallida**: la entidad queda en `pending` y no atiende peticiones hasta que la provisión termine

## ¿Cómo funciona el aislamiento en cada request?

El gateway valida el JWT (claim `tenant_slug`) y reenvía el tenant al servicio. La dependencia
`get_tenant_conn` **valida el slug con `orpycamcp_common.tenant` (un solo validador: `^[a-z0-9][a-z0-9-]{1,53}$`, falla cerrado con `400 invalid_tenant`), lo
convierte en nombre de schema y lo entrecomilla** antes de fijar el `search_path` en una conexión
`asyncpg`; el nombre de un schema no admite `$1`, así que la validación es lo que impide la inyección:

```python
schema = tenant_schema(slug)  # ^[a-z0-9][a-z0-9-]{1,53}$ -> "tenant_" + slug.replace("-", "_")
async with pool.acquire() as conn:
    await conn.execute(f'SET search_path = "{schema}", public')
```

Todas las consultas de esa conexión operan en el schema correcto. **Nunca** interpole un slug sin
validar ni sin comillas en un `SET search_path`.

## Nomenclatura de recursos

| Recurso | Patrón | Ejemplo |
|---|---|---|
| Schema PostgreSQL | `tenant_{slug con guion → guion bajo}` | `tenant_gobernacion_caldas` |
| Bucket de documentos | `orpycamcp-{slug}-documents` | `orpycamcp-gobernacion-caldas-documents` |
| Bucket de preservación (WORM) | `orpycamcp-{slug}-preservation` | `orpycamcp-gobernacion-caldas-preservation` |
| Stream Redis (compartido por todas las entidades) | `orpycamcp.{service}.events` | `orpycamcp.document.events` |

El stream **no lleva el slug**: es uno por servicio, y la entidad viaja dentro del sobre firmado del
evento (ADR-010).

<!-- TODO: translate to English -->
