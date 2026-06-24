# ADR-012: Migraciones por tenant (runner global + tenant)

**Estado**: Aceptado (implementado en F1 — runner de auth-service + plantilla)
**Fecha**: 2026-06-16
**Autores**: Giampiero (mantenedor principal)

---

## Contexto

OrpycaMCP aísla cada institución en su propio schema `tenant_{slug}` ([ADR-002](ADR-002-multi-tenancy-schema-isolation.md)). Las tablas de dominio (radicados, expedientes, RBAC, anexos, flujos…) viven **por tenant**; solo unas pocas tablas transversales viven en `public` (`tenants`, `audit_log`, `_migrations`).

Estado verificado del repositorio:

- `tenant-service` crea el schema **vacío** (`CREATE SCHEMA IF NOT EXISTS tenant_{slug}`) al dar de alta un tenant; **no** crea tablas dentro.
- Las migraciones de `document`/`archive`/`storage`/`workflow` ya están escritas para vivir en `tenant_{slug}` (crean `radicados`, `expedientes`, `files`… **sin cualificar schema**, con comentarios del tipo "aplicado tras `SET search_path = tenant_{slug}`").
- Pero el `runner.py` actual **no fija `search_path` ni itera tenants**: conecta y ejecuta el SQL tal cual, por lo que esas tablas caerían en `public`.

Es decir, el mecanismo de migración por tenant que el código da por hecho **no existe**. El RBAC de E08 lo necesita, y también todas las tablas per-tenant de la Fase 1.

## Decisión

**El `runner.py` distingue dos clases de migración por convención de carpetas y aplica cada clase a su ámbito:**

```
migrations/
  global/      → se aplican en el schema `public` (una vez)
  tenant/      → se aplican a CADA schema `tenant_{slug}` existente, fijando search_path
```

- **`global/`**: tablas/objetos transversales (p. ej. `public.audit_log`). Tracking en `public._migrations(filename)`.
- **`tenant/`**: tablas de dominio del tenant. Para cada schema `tenant_*` existente, el runner hace `SET LOCAL search_path = "tenant_{slug}", public` y aplica el SQL (que **no** cualifica schema). Tracking en `public._tenant_migrations(schema_name, filename)`.
- Ambas clases son **idempotentes** (`CREATE … IF NOT EXISTS`) y se saltan lo ya registrado.
- Los schemas de tenant se descubren con `information_schema.schemata` (`LIKE 'tenant\_%'`), de modo que el runner **no depende** de la tabla `public.tenants` (desacople entre servicios).
- Cada `tenant/` SQL se aplica dentro de **su propia transacción** por schema: si una falla, ese schema queda consistente y el runner aborta con error claro.

### Numeración

`global/` y `tenant/` numeran **independientemente** desde `001_` (el tracking de tenant es por `(schema, filename)`, no colisiona con el global). La asignación canónica de números de 00-reconciliacion-tecnica.md §2 sigue vigente dentro de cada clase.

### Provisioning de un tenant nuevo

Al crear un tenant en caliente, sus tablas se materializan **re-ejecutando el runner** de cada servicio (que detecta el schema nuevo y le aplica las migraciones `tenant/` pendientes). El provisioning automático dirigido por el evento `tenant.created` (que cada servicio consuma y corra solo lo suyo) es una **mejora posterior** (E14); no es necesaria para F1, donde el runner se ejecuta en el arranque (§6 de la reconciliación).

### Compatibilidad

El runner nuevo procesa **solo** `global/` y `tenant/`. Si encuentra `*.sql` sueltos en la raíz de `migrations/` (servicios aún no reorganizados), los **reporta y los ignora** para forzar la migración a la nueva convención. auth-service se reorganiza en este ADR; `document`/`archive`/`storage`/`workflow` se reorganizan al implementar sus épicas (E01/E02/E05/E07).

## Consecuencias

**Positivas:**
- Un único mecanismo, en SQL crudo + asyncpg ([ADR-003](ADR-003-asyncpg-sin-orm.md)), sin dependencias nuevas.
- El runner es idempotente y re-ejecutable; encaja con la decisión de aplicarlo en el arranque.
- Desacoplado de `tenant-service` (descubre schemas, no consume su API).

**Negativas:**
- Un tenant creado en caliente no tiene sus tablas hasta re-ejecutar los runners (mitigable con provisioning event-driven en E14).
- Si hay muchos tenants, el runner itera todos en cada arranque (barato: salta lo ya aplicado por el tracking).
- Requiere reorganizar las migraciones existentes a `global/`+`tenant/` (deuda acotada, servicio por servicio).

## Alternativas consideradas

- **Provisioning event-driven** (cada servicio consume `tenant.created` y aplica su DDL): más desacoplado pero más piezas móviles (un consumer y backfill por servicio). Se adopta como evolución en E14, no como base.
- **tenant-service orquesta el DDL de todos**: centraliza pero acopla tenant-service a los esquemas de todos los servicios. Descartada.

## Relacionados

- [ADR-002](ADR-002-multi-tenancy-schema-isolation.md) — aislamiento por schema que este runner materializa.
- [ADR-003](ADR-003-asyncpg-sin-orm.md) — SQL crudo + asyncpg.
- [ADR-008](ADR-008-auditoria-inmutable.md) / [ADR-010](ADR-010-libreria-compartida.md) — `audit_log` es una migración `global/`.
- E08 (RBAC) es la primera consumidora de migraciones `tenant/`.
