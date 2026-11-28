# Multi-tenancy / Multi-tenancy

OrpycaMCP soporta múltiples instituciones en una sola instalación usando **schema isolation** en PostgreSQL.

## ¿Qué hace?
Cada institución (tenant) tiene su propio schema de base de datos (`tenant_{slug}`), completamente aislado de los demás. El schema `public` solo contiene el registro de instituciones.

## ¿Por qué existe?
- Permite que una entidad gubernamental regional sirva a múltiples municipios
- Reduce costos operativos respecto a instalaciones separadas
- Garantiza aislamiento de datos (cada tenant no puede ver datos de otros)

## ¿Cómo agregar una institución?

Una instalación sirve a **una entidad** (ADR-047 §3); el schema por entidad se conserva porque cuesta
una sentencia por conexión y deja abierta la opción de alojar varias. El alta es una **operación de
plataforma**, no de aplicación: no hay ruta HTTP para ella. La hace `scripts/init_tenant.py` (que
`scripts/init-tenant.sh` ejecuta en un contenedor, con el rol propietario de la base):

```bash
./scripts/init-tenant.sh gobernacion-caldas "Gobernación de Caldas" GCAL
```

Esto:
1. Valida el slug (`^[a-z0-9-]+$`: minúsculas, dígitos y **guion**, sin guion bajo) y lo registra en `public.tenants`
2. Crea el schema `tenant_gobernacion_caldas` (el guion pasa a guion bajo) y el `GRANT USAGE` del rol de aplicación
3. Aplica la cadena de migraciones de `services/docuboto/migrations/` (`global/` a `public`, `tenant/` al esquema nuevo) y la anota en el libro `public.schema_migrations` (ADR-053)

La app opera con el rol de aplicación y **no ejecuta DDL**. Los buckets (`orpycamcp-{slug}-documents`,
y los de preservación) los crea el paquete `storage` la primera vez que los necesita; ADR-050 retira el
almacén de objetos.

## ¿Qué puede salir mal?

- **Slug duplicado**: el sistema devuelve `409 Conflict`
- **Slug no válido** (mayúsculas, guion bajo, guion inicial, menos de 2 o más de 54 caracteres; el mismo validador que todos los servicios, `orpycamcp_common.tenant`): `422`; el schema se llama `tenant_{slug}` y PostgreSQL trunca los identificadores a 63 bytes sin avisar
- **Schema ya existe**: el provisioning es idempotente, no falla
- **Migración fallida**: sin schema la entidad responde `503 tenant_no_provisionado` hasta que la provisión termine

## ¿Cómo funciona el aislamiento en cada request?

`UsuarioActualMiddleware` valida el JWT una vez por petición y deja el slug (claim `tenant_slug`) en
`request.state`; un `X-Tenant-Slug` que mande el cliente se borra. La dependencia
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
| Stream de eventos (columna `stream` de la tabla `eventos` del schema de la entidad) | `orpycamcp.{service}.events` | `orpycamcp.document.events` |

El stream **no lleva el slug**: es uno por servicio, y desde ADR-049 los eventos viven en el schema
de la entidad; el sobre firmado (ADR-010) sigue llevando la entidad, que es la que verifica el consumidor.

## Estado de la entidad

Sin estados de ciclo de vida (ADR-047 §3): `public.tenants` es la fila de configuración de la
entidad. Se atiende si existe su fila y su schema; si falta la fila, `403 tenant_inactivo`, y si falta
el schema, `503 tenant_no_provisionado`. `get_tenant_conn` y `get_vetted_conn`
(`orpycamcp_common.database`) lo comprueban antes de ceder la conexión. Las rutas públicas responden
el mismo `404` a una entidad inexistente o sin schema. Ya no hay entidad suspendida ni excepciones de
conservación.
Detalle y alternativas en [ADR-035](adr/ADR-035-estado-de-la-entidad-cumplimiento-en-el-borde.md).
