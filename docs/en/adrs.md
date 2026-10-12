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
| [ADR-028](adr/ADR-028-el-flujo-mueve-el-estado-del-radicado.md) | The workflow moves the radicado's status: one pure function written in the same transaction as the step, closing only via an explicit "Finish processing", `closed`/`anulado` terminal (409) and holder-only `marcar_leido`; audit remediation: clearance and holder gates on `assign`/`transfer`, annulment cancels the step, immutable backfill evidence | Implemented |
| [ADR-029](adr/ADR-029-ubicacion-actual-del-radicado.md) | Current location of the radicado: three `flujo_*` columns written by workflow-service in the same `UPDATE` as the status, two generated `ubicacion_*` columns (computed as pairs), one pure `derivar_ubicacion` function (last step by when it closed, not by its number), code only when the name is unique, backfill with insert-only evidence, and kept out of everything public | Implemented |
| [ADR-031](adr/ADR-031-constancia-autorizacion-ip-firmada-politica-por-entidad.md) | Authorization record: gateway-signed IP on public routes, per-entity data-processing policy served and re-read by the server, and a delivery receipt with author and no reuse | Accepted |
| [ADR-032](adr/ADR-032-dependencia-por-codigo.md) | The dependencia is correlated by code, not by name: immutable `codigo` (trigger + 422), a single `resolver_codigo_dependencia` function with name history, code-canonical contract with the name as deprecated alias, fail-closed backfill on ambiguity, deactivate/delete by code | Accepted |
| [ADR-033](adr/ADR-033-entrada-de-produccion-nginx-en-contenedor.md) | Production entry: containerised nginx (`edge`) as the only service with ports, `TRUSTED_PROXIES` without the network's gateway so the sealed IP cannot be forged, Keycloak published only on `/realms/` and `/resources/`, and third-party images pinned by tag and digest | Accepted |
| [ADR-034](adr/ADR-034-xmldsig-conforme-firma-verificable-por-terceros.md) | The XAdES signature is declared as it is applied: explicit `Transforms` on the references `signxml` leaves without them, `SignedInfo` signed after the patch (seal and personal signature), fail-closed self-verification with a default `XAdESVerifier()`, no canonicalisation shortcut when verifying, and `ArchiveTimeStamp` input per EN 319 132-1 §5.5.2; validated against EU DSS | Accepted |

## How to propose a new ADR

1. Create the file `docs/es/adr/ADR-{NNN}-{slug}.md`
2. Use the standard format: Context, Decision, Consequences, Alternatives
3. Update this page with the new ADR
4. Open an MR for discussion before marking it as "Accepted"
