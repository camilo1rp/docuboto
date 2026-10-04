# ADR-008: Auditoría inmutable con `audit_log` encadenado por hash

**Estado**: Propuesto
**Fecha**: 2026-06-15
**Autores**: Giampiero (mantenedor principal)

---

## Contexto

La Ley 594/2000 y el Acuerdo AGN 001/2024 exigen **trazabilidad completa** del ciclo de vida documental. Para tener valor probatorio, la auditoría debe ser **a prueba de manipulación** (*tamper-evident*): si alguien altera o borra un registro de auditoría, debe poder detectarse.

En OrpycaMCP **todos los servicios** producen auditoría (radicación, flujos, expedientes, anulaciones, disposición, etc.). Hace falta:

1. Un destino **transversal y consistente** para la auditoría de todos los servicios.
2. **Inmutabilidad** verificable.
3. Soporte **multi-tenant** ([ADR-002](ADR-002-multi-tenancy-schema-isolation.md)) sin perder la capacidad de consultas/operación globales.

Hoy no existe ni el esquema canónico de `audit_log` ni una librería común de auditoría; cada servicio la implementaría a su manera, lo que rompería la consistencia.

## Decisión

**Una única tabla `audit_log` en el schema `public`, append-only, particionada por tiempo, con encadenamiento de hash por tenant, escrita por una librería de auditoría compartida.**

- **Append-only**: la aplicación no puede `UPDATE` ni `DELETE` sobre `audit_log`.
- **Encadenamiento de hash**: cada fila guarda `hash = sha256(prev_hash || contenido_canónico)`. Alterar una fila rompe la cadena de todas las posteriores → manipulación evidente.
- **Cadena por `tenant_slug`**: cada tenant tiene su propia cadena, para permitir escrituras concurrentes entre tenants sin contención global.
- **Librería compartida** (`audit.append(...)`) que todos los servicios usan; nadie escribe `audit_log` "a mano".

## Implementación

### Esquema (en `public`)

```sql
-- public.audit_log — append-only, particionada por mes, cadena por tenant
CREATE TABLE IF NOT EXISTS public.audit_log (
    id           BIGINT GENERATED ALWAYS AS IDENTITY,
    tenant_slug  TEXT        NOT NULL,
    ts           TIMESTAMPTZ NOT NULL DEFAULT now(),
    service      TEXT        NOT NULL,           -- 'document-service', ...
    actor        TEXT,                           -- user_id (o 'system')
    canal        TEXT        NOT NULL DEFAULT 'api', -- 'api' | 'ui' | 'mcp' | 'system' (la
                                                 -- aplicación lo declara SIEMPRE: `audit.append`
                                                 -- no tiene valor por omisión, AREA-03-07)
    action       TEXT        NOT NULL,           -- 'radicado.creado', 'expediente.cerrado', ...
    object_type  TEXT        NOT NULL,
    object_ref   TEXT,                           -- tracking/uuid del objeto
    payload      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    prev_hash    TEXT,                           -- hash de la fila previa de ESTE tenant
    hash         TEXT        NOT NULL,           -- sha256(prev_hash || contenido_canónico)
    PRIMARY KEY (id, ts)
) PARTITION BY RANGE (ts);

-- Particiones mensuales (creadas por job/migración)
-- Índices por tenant y por objeto para consulta de trazabilidad.
CREATE INDEX ix_audit_tenant_ts ON public.audit_log (tenant_slug, ts);
CREATE INDEX ix_audit_object   ON public.audit_log (object_type, object_ref);
```

### Inmutabilidad

- El rol de aplicación recibe **solo `INSERT` y `SELECT`** sobre `audit_log` (sin `UPDATE`/`DELETE`).
- Un **trigger** `BEFORE UPDATE OR DELETE` que lanza excepción, como segunda barrera.
- La retención/purga (cuando la TRD lo permita) se hace con una cuenta administrativa separada y queda, a su vez, auditada.

### Librería compartida

```python
# Pseudocódigo: append toma el último hash del tenant, calcula el nuevo y lo inserta.
async def append(conn, *, tenant, service, actor, action, object_type, object_ref, payload):
    prev = await conn.fetchval(
        "SELECT hash FROM public.audit_log WHERE tenant_slug=$1 ORDER BY id DESC LIMIT 1", tenant)
    h = sha256(canonical(prev, tenant, action, object_type, object_ref, payload))
    await conn.execute("INSERT INTO public.audit_log (...) VALUES (...)", ..., prev, h)
```

### Verificación

Un job periódico recalcula la cadena por tenant y reporta cualquier ruptura (alerta técnica — ver catálogo de alertas).

## Consecuencias

**Positivas:**
- Auditoría **centralizada, consistente y verificable** para todos los servicios.
- Manipulación **detectable** (cadena de hash).
- Consultas de trazabilidad por objeto y por tenant.

**Negativas:**
- La cadena **serializa** los `append` dentro de un mismo tenant (mitigado: cadena por tenant, no global).
- `audit_log` en `public` es **cross-tenant**: hay que justificar el acceso y filtrar siempre por `tenant_slug` (la operación global lo necesita; el dato sensible va en `payload` mínimo).
- Crecimiento de almacenamiento → particionado mensual + política de retención alineada con la TRD.

## Alternativas consideradas

- **Tabla de auditoría por tenant** (en `tenant_{slug}`): descartada como ubicación principal por dificultar consultas globales y verificación operativa; se conserva su ventaja (paralelismo) aplicando **cadena por tenant** dentro de la tabla única.
- **Solo WORM de almacenamiento (MinIO Object Lock)**: válido para los **documentos** (lo cubre la preservación, E10), pero la auditoría necesita ser **consultable**; no basta un objeto inmutable.
- **Cadena de bloques / servicio externo de sellado**: descartado por sobre-ingeniería para el alcance actual; el encadenamiento de hash + append-only + verificación cubre el requisito.

## Relacionados

- [ADR-002](ADR-002-multi-tenancy-schema-isolation.md) — multi-tenancy; `audit_log` vive en `public` con `tenant_slug`.
- [ADR-003](ADR-003-asyncpg-sin-orm.md) — SQL crudo; la librería de auditoría usa asyncpg.
- [ADR-009](ADR-009-historial-flujos-eventos.md) — el historial de flujos sigue la misma filosofía append-only.
