# ADR-014: Búsqueda con PostgreSQL FTS (no Elasticsearch)

**Estado**: Aceptado
**Fecha**: 2026-06-21
**Autores**: Giampiero (mantenedor principal)

---

## Contexto

La Fase 2 (E09 — búsqueda, consulta y reportes) exige búsqueda avanzada sobre radicados y expedientes: texto completo con ranking, coincidencia difusa/prefijo, filtros por campos (incluidos metadatos `JSONB`), rangos de fecha, y reportes/estadísticas. El SGDEA debe permitir que un operador encuentre cualquier radicado/expediente.

El motor de almacenamiento es **PostgreSQL con aislamiento por schema `tenant_{slug}`** ([ADR-002](ADR-002-multi-tenancy-schema-isolation.md)) y acceso con **asyncpg + SQL crudo** ([ADR-003](ADR-003-asyncpg-sin-orm.md)). La base ya tiene búsqueda full-text nativa funcionando: columna generada `search_vector tsvector` con `setweight`/`to_tsvector('spanish', …)`, índice **GIN**, e índices **trigram** (`pg_trgm`) para coincidencia difusa (`document-service/migrations/tenant/003_search_indexes.sql`, `routers/search.py` con `websearch_to_tsquery` + `ts_rank` + fallback `ILIKE`/trigram). Los metadatos variables viven en `metadata JSONB` con índice GIN ([ADR-007](ADR-007-metadatos-jsonb.md)).

La pregunta es si introducir **Elasticsearch/OpenSearch** como motor de búsqueda dedicado, o consolidar sobre las capacidades **nativas de PostgreSQL**.

## Decisión

**Implementar la búsqueda de OrpycaMCP sobre PostgreSQL nativo (FTS `tsvector` + `pg_trgm` + filtros `JSONB`/GIN), sin un motor de búsqueda externo en F2.**

- **Texto completo**: `to_tsvector('spanish', …)` con pesos (`A`=número de radicado, `B`=asunto, …), consulta con `websearch_to_tsquery`, ranking con `ts_rank`/`ts_rank_cd`, índice GIN sobre `search_vector`.
- **Difuso/prefijo**: `pg_trgm` (índices GIN `gin_trgm_ops`) y `unaccent` para normalizar acentos.
- **Filtros estructurados y por metadato**: `WHERE` sobre columnas + contención `metadata @> $jsonb` (índice GIN), rangos de fecha, estado, dependencia, tipo.
- **Reportes/estadísticas**: agregaciones SQL (`GROUP BY`, `COUNT`, ventanas) sobre el schema del tenant.
- El **índice es derivado de la BD** (la fila es la verdad): no se mantiene un store paralelo que pueda divergir.

## Consecuencias

**Positivas:**
- **Cero infraestructura nueva**: sin cluster Elasticsearch que desplegar, asegurar, versionar ni sincronizar. Coherente con el objetivo de despliegue sencillo del proyecto.
- **Aislamiento multi-tenant trivial**: la búsqueda corre en `tenant_{slug}` como cualquier otra consulta (ADR-002). Con Elasticsearch habría que replicar el aislamiento (índice/alias por tenant + control de acceso) fuera del modelo ya probado.
- **Consistencia fuerte**: el `search_vector` es una columna generada; no hay *reindexado* asíncrono ni ventana de inconsistencia BD↔índice.
- **Una sola fuente de datos y un solo lenguaje (SQL)**: filtros, texto y agregaciones en la misma consulta; sin ETL ni *connectors*.
- Suficiente para la escala objetivo (instituciones públicas/privadas latinoamericanas): GIN + trigram rinden bien hasta millones de filas por tenant.

**Negativas:**
- PostgreSQL FTS es menos potente que Elasticsearch en *relevancia avanzada*, sinónimos, *fuzziness* configurable, facetado a gran escala y agregaciones analíticas masivas.
- El diccionario `spanish` cubre stemming básico; necesidades lingüísticas avanzadas (sinónimos, *thesaurus*) requieren configurar diccionarios propios.
- A escalas muy grandes (decenas de millones de docs con consultas analíticas intensivas) podría requerirse un motor dedicado.

**Mitigación / puerta de salida:** si una institución supera lo que PostgreSQL FTS rinde, se podrá añadir un motor externo **alimentado por eventos** (`orpycamcp.*.events`, ADR-010) como índice derivado opcional por despliegue, sin cambiar el modelo de datos ni el contrato de la API de búsqueda. La decisión es reversible por diseño.

## Alternativas consideradas

- **Elasticsearch/OpenSearch dedicado**: descartado en F2 por el coste operativo (cluster, seguridad, sincronización), la fricción con el aislamiento por schema (ADR-002) y el riesgo de divergencia BD↔índice; su potencia extra no es necesaria a la escala objetivo.
- **`LIKE`/`ILIKE` simple sin índices**: descartado por no escalar (escaneos secuenciales) y no ofrecer ranking.
- **Extensiones especializadas (p. ej. `pgvector` para semántica)**: fuera de alcance de E09; la búsqueda semántica/RAG pertenece a la capa de conocimiento (E21, [ADR-006](ADR-006-proveedor-ia-configurable.md)), no a la búsqueda documental de F2.

## Relacionados

- [ADR-002](ADR-002-multi-tenancy-schema-isolation.md) — la búsqueda corre por `tenant_{slug}`.
- [ADR-003](ADR-003-asyncpg-sin-orm.md) — SQL crudo; las consultas de búsqueda son SQL explícito.
- [ADR-007](ADR-007-metadatos-jsonb.md) — filtro por campo de metadato vía contención `JSONB`/GIN.
- [ADR-010](ADR-010-libreria-compartida.md) — la puerta de salida (índice externo) se alimentaría por eventos.
