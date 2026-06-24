# Decisiones de Arquitectura (ADRs)

Los ADRs (Architecture Decision Records) documentan las decisiones técnicas importantes: qué se decidió, por qué, y qué alternativas se descartaron.

Los archivos completos están en `docs/es/adr/`.

## Índice

| ADR | Título | Estado |
|---|---|---|
| [ADR-001](adr/ADR-001-microservices-architecture.md) | Arquitectura de microservicios con FastAPI | Aceptado |
| [ADR-002](adr/ADR-002-multi-tenancy-schema-isolation.md) | Multi-tenancy con schema isolation en PostgreSQL | Aceptado |
| [ADR-003](adr/ADR-003-asyncpg-sin-orm.md) | asyncpg directo en lugar de SQLAlchemy ORM | Aceptado |
| [ADR-005](adr/ADR-005-batch-api.md) | API de operaciones por lotes (batch) | Aceptado |
| [ADR-006](adr/ADR-006-proveedor-ia-configurable.md) | Proveedor de IA configurable (multi-proveedor, global) para la capa de conocimiento | Propuesto |
| [ADR-007](adr/ADR-007-metadatos-jsonb.md) | Metadatos documentales con JSONB validado por plantilla | Propuesto |
| [ADR-008](adr/ADR-008-auditoria-inmutable.md) | Auditoría inmutable con `audit_log` encadenado por hash | Propuesto |
| [ADR-009](adr/ADR-009-historial-flujos-eventos.md) | Historial de flujos como eventos append-only + proyección | Propuesto |
| [ADR-010](adr/ADR-010-libreria-compartida.md) | Librería compartida `orpycamcp_common` (auditoría y eventos) | Aceptado |
| [ADR-011](adr/ADR-011-frontend-sveltekit-monorepo.md) | Frontend SvelteKit (SSR) como app de presentación en el monorepo | Propuesto |
| [ADR-012](adr/ADR-012-migraciones-por-tenant.md) | Migraciones por tenant (runner `global/` + `tenant/`) | Aceptado |
| [ADR-013](adr/ADR-013-autorizacion-resuelta-en-bd.md) | Autorización resuelta en la BD por request (Keycloak autentica, la BD autoriza) | Aceptado |
| [ADR-014](adr/ADR-014-busqueda-postgresql-fts.md) | Búsqueda con PostgreSQL FTS (tsvector + pg_trgm + JSONB), no Elasticsearch | Aceptado |
| [ADR-015](adr/ADR-015-trd-ccd-modelo.md) | Modelo TRD/CCD: jerarquía serie↔subserie, retención en dos fases, disposición AGN (CT/E/S/M) | Aceptado |
| [ADR-016](adr/ADR-016-firma-electronica.md) | Firma electrónica nativa (hash SHA-256 + identidad + sello de tiempo), PKI/PDF diferida | Aceptado |
| [ADR-017](adr/ADR-017-archivo-fisico.md) | Archivo físico: ubicación (dirección recursiva) ≠ unidad de conservación (contenedor móvil), patrón ArchivesSpace | Aceptado |
| [ADR-018](adr/ADR-018-interoperabilidad-webhooks.md) | Interoperabilidad saliente por webhooks HTTP firmados (HMAC-SHA256) sobre el bus de eventos, suscritos por tenant | Aceptado |
| [ADR-019](adr/ADR-019-capa-mcp.md) | Capa MCP como fachada fina cliente del gateway (catálogo de tools declarativo, sin lógica ni BD) | Aceptado |
| [ADR-020](adr/ADR-020-sistema-diseno-orpyca-asistente.md) | Sistema de diseño Orpyca (tokens Sass + custom properties, branding por tenant) y asistente conversacional (texto/voz) como traductor a tools MCP con IA local opt-in | Propuesto |

## Cómo proponer un nuevo ADR

1. Crea el archivo `docs/es/adr/ADR-{NNN}-{slug}.md`
2. Usa el formato estándar: Contexto, Decisión, Consecuencias, Alternativas
3. Actualiza esta página con el nuevo ADR
4. Abre un MR para discusión antes de marcar como "Aceptado"

<!-- TODO: translate to English -->
