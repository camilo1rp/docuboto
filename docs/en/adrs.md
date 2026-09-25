# Architecture Decision Records (ADRs)

ADRs (Architecture Decision Records) document important technical decisions: what was decided, why, and which alternatives were discarded.

The full files are in `docs/es/adr/`.

## Index

| ADR | Title | Status |
|---|---|---|
| [ADR-001](adr/ADR-001-microservices-architecture.md) | Microservices architecture with FastAPI | Accepted |
| [ADR-002](adr/ADR-002-multi-tenancy-schema-isolation.md) | Multi-tenancy with schema isolation in PostgreSQL | Accepted |
| [ADR-003](adr/ADR-003-asyncpg-sin-orm.md) | Direct asyncpg instead of SQLAlchemy ORM | Accepted |
| [ADR-005](adr/ADR-005-batch-api.md) | Batch operations API | Accepted |
| [ADR-006](adr/ADR-006-proveedor-ia-configurable.md) | Configurable AI provider (multi-provider, global) for the knowledge layer | Proposed |
| [ADR-007](adr/ADR-007-metadatos-jsonb.md) | Document metadata with template-validated JSONB | Proposed |
| [ADR-008](adr/ADR-008-auditoria-inmutable.md) | Immutable audit with hash-chained `audit_log` | Proposed |
| [ADR-009](adr/ADR-009-historial-flujos-eventos.md) | Flow history as append-only events + projection | Proposed |
| [ADR-010](adr/ADR-010-libreria-compartida.md) | Shared library `orpycamcp_common` (audit and events) | Accepted |
| [ADR-011](adr/ADR-011-frontend-sveltekit-monorepo.md) | SvelteKit frontend (SSR) as the presentation app in the monorepo | Proposed |
| [ADR-012](adr/ADR-012-migraciones-por-tenant.md) | Per-tenant migrations (runner `global/` + `tenant/`) | Accepted |
| [ADR-013](adr/ADR-013-autorizacion-resuelta-en-bd.md) | Authorization resolved in the DB per request (Keycloak authenticates, the DB authorizes) | Accepted |
| [ADR-014](adr/ADR-014-busqueda-postgresql-fts.md) | Search with PostgreSQL FTS (tsvector + pg_trgm + JSONB), not Elasticsearch | Accepted |
| [ADR-015](adr/ADR-015-trd-ccd-modelo.md) | TRD/CCD model: series↔subseries hierarchy, two-phase retention, AGN disposition (CT/E/S/M) | Accepted |
| [ADR-016](adr/ADR-016-firma-electronica.md) | Native electronic signature (SHA-256 hash + identity + timestamp), PKI/PDF deferred | Accepted |
| [ADR-017](adr/ADR-017-archivo-fisico.md) | Physical archive: location (recursive address) ≠ conservation unit (mobile container), ArchivesSpace pattern | Accepted |
| [ADR-018](adr/ADR-018-interoperabilidad-webhooks.md) | Outgoing interoperability via signed HTTP webhooks (HMAC-SHA256) over the event bus, subscribed per tenant | Accepted |
| [ADR-019](adr/ADR-019-capa-mcp.md) | MCP layer as a thin gateway-client facade (declarative tool catalog, no logic or DB) | Accepted |
| [ADR-020](adr/ADR-020-sistema-diseno-orpyca-asistente.md) | Orpyca design system (Sass tokens + custom properties, per-tenant branding) and conversational assistant (text/voice) as a translator to MCP tools with local opt-in AI | Proposed |
| [ADR-021](adr/ADR-021-reclamacion-deadletter-eventos.md) | PEL reclamation and per-consumer-group dead-letter in Redis Streams (signature/workflow/notification-service) | Accepted |
| [ADR-022](adr/ADR-022-indice-electronico-v2-metadatos.md) | Electronic index v2 profile — RT-15 metadata per document (format, size, foliation) | Accepted |
| [ADR-023](adr/ADR-023-worm-object-lock-preservacion.md) | WORM / Object-Lock for preservation (MinIO) and the AIP/PREMIS roadmap | Accepted (Increment A) |
| [ADR-024](adr/ADR-024-root-fuera-de-banda.md) | `USUA_PERM_ROOT` is never granted through the API — an entity administrator is not the ceiling of their own entity; ROOT is provisioned out of band only | Accepted |
| [ADR-025](adr/ADR-025-trd-instrumento-convalidado-versionado.md) | The TRD is a convalidated instrument, not a configuration table: append-only versioning and pinning of the version that governs each expediente | Implemented (Increments 1+2) |
| [ADR-026](adr/ADR-026-tvd-fondo-acumulado.md) | The TVD is the same instrument with different content: a `tipo_instrumento` discriminator on `trd_series`, not its own table (registration and convalidation circuit for an accumulated fonds) | Implemented (Increment 1) |
| [ADR-027](adr/ADR-027-sustitucion-almacen-objetos-rustfs.md) | MinIO archived its open-source project and pulled its images: the object store moves to **RustFS**, and the engine is chosen by a WORM conformance battery against a real S3 (RustFS 7/7, MinIO 7/7, Garage 0/7), not by a datasheet | Implemented |

## How to propose a new ADR

1. Create the file `docs/es/adr/ADR-{NNN}-{slug}.md`
2. Use the standard format: Context, Decision, Consequences, Alternatives
3. Update this page with the new ADR
4. Open an MR for discussion before marking it as "Accepted"
