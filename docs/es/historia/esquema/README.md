# Historia del esquema

Estas carpetas guardan las cadenas de migraciones que tenía cada servicio antes de
[ADR-053](../../adr/ADR-053-esquema-legible-y-lanzamientos.md). **No se ejecutan.** Ningún
runner las lee y ninguna prueba depende de ellas.

La cadena vigente está en `services/docuboto/migrations/`: `global/` va a `public` y
`tenant/` a cada esquema `tenant_*`.

## Para qué sirven

Los 286 archivos numerados llevaban una instalación antigua hasta el esquema actual, paso
a paso. No hay ninguna instalación con datos, así que esa función ya no tiene a quién
servir. Se conservan por sus comentarios: explican por qué existe cada índice, cada
disparador y cada restricción, y qué defecto corrigió cada cambio.

## Cómo están ordenadas

Una carpeta por dominio, con los nombres de archivo de entonces:

| Carpeta | Servicio de origen |
|---|---|
| `auth/` | auth-service |
| `tenant/` | tenant-service |
| `document/` | document-service |
| `storage/` | storage-service |
| `workflow/` | workflow-service |
| `archive/` | archive-service |
| `signature/` | signature-service |
| `notification/` | notification-service |
| `knowledge/` | knowledge-service |

Dentro de cada una, `global/` iba a `public` y `tenant/` a cada esquema de entidad. El
orden de aplicación era el de la tabla, y dentro de cada carpeta, el numérico.

## Cómo se comprobó el corte

Se aplicaron las dos cadenas a bases vacías de PostgreSQL 17 y se comparó
`pg_dump --schema-only` de cada una:

- El esquema de entidad es idéntico byte a byte, y también lo son los datos sembrados.
- En `public` hay cuatro diferencias, todas deliberadas:
    - No existen `public.auth_audit_logs` (obsoleta desde ADR-010), `public._migrations`
      ni `public._tenant_migrations` (libros anteriores a `public.schema_migrations`).
    - No se instala `pgcrypto`: solo la usaba un *backfill* de huellas de certificados.
    - `chk_audit_log_hash_forma` nace validada y dentro del `CREATE TABLE`. Antes era
      `NOT VALID` por filas anteriores que ya no existen.
    - Las particiones mensuales de `audit_log` rechazan `TRUNCATE` con
      `reject_audit_log_mutation_d03()`, que es lo que hace `harden_audit_partition`.
      Antes las cuatro primeras conservaban `reject_audit_log_truncate()` por el orden
      en que se crearon.
