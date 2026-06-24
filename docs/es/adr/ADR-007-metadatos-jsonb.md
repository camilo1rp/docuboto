# ADR-007: Metadatos documentales con JSONB validado por plantilla

**Estado**: Propuesto
**Fecha**: 2026-06-15
**Autores**: Giampiero (mantenedor principal)

---

## Contexto

Cada **tipo documental** del SGDEA tiene metadatos distintos (una resolución no lleva los mismos campos que una factura o una historia laboral). El número de tipos crece con cada institución y con la TRD de cada tenant, de modo que el conjunto de campos **no es fijo ni conocido de antemano**.

El Orfeo legado resolvió esto con un modelo **EAV** (entidad-atributo-valor): tablas `sgd_def_meta*` que guardaban cada campo como una fila `(documento, atributo, valor)`. Ese enfoque permite flexibilidad, pero en la práctica produjo:

- Consultas con múltiples `JOIN`/auto-joins para reconstruir un solo documento.
- Valores sin tipado (todo `TEXT`), con validación dispersa o inexistente.
- Dificultad para indexar y para reportar.

A la vez, la migración de ICETEX ya había probado con éxito almacenar metadatos como **JSONB**. El requisito (RT-03) pide metadatos flexibles **por tipo documental**, validados y consultables, sin migración de esquema cada vez que se añade un tipo.

## Decisión

**Almacenar los metadatos variables de radicados y expedientes en una columna `metadata JSONB`, validada en la capa de servicio contra una plantilla por tipo documental.**

- La definición de campos por tipo vive en una tabla **`metadata_templates`** (esquema JSON + versión, por `tipo_documental`).
- La validación ocurre en el **service layer** (no en el ORM —no hay ORM, ver [ADR-003](ADR-003-asyncpg-sin-orm.md)— sino con validación de esquema explícita).
- La columna `metadata` se indexa con **GIN** para permitir filtros y búsqueda por campos.

## Implementación

### Modelo de datos

```sql
-- Plantilla de metadatos por tipo documental (en tenant_{slug})
CREATE TABLE IF NOT EXISTS metadata_templates (
    id                 UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tipo_documental_id UUID NOT NULL,
    version            INT  NOT NULL DEFAULT 1,
    json_schema        JSONB NOT NULL,          -- JSON Schema de los campos válidos
    activo             BOOLEAN NOT NULL DEFAULT true,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tipo_documental_id, version)
);

-- En la tabla de radicados/expedientes
ALTER TABLE radicados ADD COLUMN metadata JSONB NOT NULL DEFAULT '{}'::jsonb;
CREATE INDEX ix_radicados_metadata_gin ON radicados USING GIN (metadata);
```

### Validación (capa de servicio)

```python
# Pseudocódigo: al crear/actualizar, validar metadata contra el json_schema
# de la plantilla activa del tipo documental (jsonschema). El acto se rechaza
# (422) si no valida. La versión de plantilla usada se guarda junto al dato.
validate(metadata, template.json_schema)  # lanza error de validación si falla
```

### Consulta

```sql
-- Filtro por campo de metadato (usa el índice GIN)
SELECT * FROM radicados WHERE metadata @> '{"area": "juridica"}';
```

## Consecuencias

**Positivas:**
- Añadir o cambiar un tipo documental **no requiere migración** de esquema: solo una nueva plantilla.
- Un documento se lee en **una sola fila**, sin reconstruir desde EAV.
- Consultable e indexable (GIN) por campos de metadato.
- Validación explícita y versionada por tipo documental.

**Negativas:**
- La validación vive en la **aplicación**, no en la base de datos (un `INSERT` directo podría saltarse la plantilla — mitigado restringiendo el acceso a través del service layer).
- JSONB es menos normalizado: integridad referencial entre campos de metadato no la impone el motor.
- Cambios de esquema entre versiones de plantilla exigen una estrategia de compatibilidad (se guarda la versión usada por dato).

## Alternativas consideradas

- **EAV (modelo del Orfeo legado)**: descartado por la complejidad de consulta (multi-join), la ausencia de tipado y la mala experiencia de reporte e indexación.
- **Columnas fijas por tipo documental**: descartado porque exige migración de esquema por cada tipo nuevo y no escala a metadatos definidos por el tenant.
- **Almacén documental NoSQL aparte**: descartado por no encajar con el stack (PostgreSQL) ni con el aislamiento multi-tenant por schema ([ADR-002](ADR-002-multi-tenancy-schema-isolation.md)).

## Relacionados

- [ADR-002](ADR-002-multi-tenancy-schema-isolation.md) — las plantillas y los metadatos viven en `tenant_{slug}`.
- [ADR-003](ADR-003-asyncpg-sin-orm.md) — SQL crudo; la validación de metadatos es explícita en el service layer.
