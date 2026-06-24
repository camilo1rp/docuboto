# Roadmap

> Updated as of 2026-06-22.

OrpycaMCP has followed **two complementary plans**:

1. **Foundational roadmap** (Phases 1–6, below "Scaffolding"): stood up the 8 microservices with their basic CRUD, infrastructure and CI/CD. **Complete.**
2. **specDrive plan** (Phases F1–F6, 21 epics, 138 RF): raises the scaffolding to **SGDEA conformance** (Ley 594/2000 — Colombia's General Archives Law; Acuerdos AGN 001/2024, 042/2002, 003/2015 — General Archive of the Nation agreements; ISO 14721/16363). It is the current plan and its status is detailed first.

> **Glossary:** *SGDEA* (Sistema de Gestión de Documentos Electrónicos de Archivo — electronic records management system); *RF* (requisito funcional — functional requirement).

---

## specDrive plan — SGDEA conformance (current)

**Global status:** core of the **21 epics implemented, tested in Docker (~300 passing tests across 11 microservices) and with migrations validated against real PostgreSQL/pgvector.** 20 ADRs accepted.

| Phase | Epics | Status |
|---|---|---|
| **F1 — Foundations** | common library (ADR-010), per-tenant migrations (ADR-012), E14 administration, E08 security (RBAC + audit + URD + classification), E01 radicación, E03 metadata, E07 attachments, E16 notifications, E05 flows | ✅ |
| **F2 — Query** | E09 advanced search + reports + expediente search, E13 public query by verification code, E19 IMAP email ingestion | ✅ |
| **F3 — Lifecycle** | E04 TRD/CCD (hierarchy + two-phase retention + AGN disposition), E02 expedientes (closure/transfer/foliation), E06 electronic signature (hash+identity) | ✅ |
| **F4 — Conformance** | E15 electronic index (append-only versioned XML + per-document fingerprint + verification) | ✅ core |
| **F5 — Preservation / physical** | E17 physical archive (locations, units, call number, loans, history, **FUID**), E12 primary/secondary transfers, E11 interoperability (signed webhooks), E10 preservation (plan + PREMIS events) | ✅ core |
| **F6 — Intelligence** | E18 mcp-server (tool catalog over the gateway), E21 knowledge-service (pgvector + semantic retrieval with ACL pre-filtering) | ✅ core |

### specDrive pending items (depend on external integrations/infrastructure)

- **E18**: binding the official MCP SDK (stdio / HTTP-streamable, `initialize`/capabilities).
- **E21**: event-driven ingestion, RAG grounded with citations, real embeddings/LLM provider (currently a deterministic local stub).
- **E10**: AIP/BagIt packaging, real Object Lock/WORM on MinIO, PDF/A validation with veraPDF.
- **E15**: XAdES signing of the index at closure (requires a cryptographic signature-service, ADR-016).
- **E20**: integration with postal operators (feasible as a webhook consumer, ADR-018).
- **E17** (minor): PDF/QR label, capacity/occupancy, external custody.

---

## Foundational scaffolding (Phases 1–6) ✅ Complete

> These phases built the base on which specDrive raises conformance.

### Phase 1 — Framework and Architecture
- [x] Structure, CLAUDE.md, agents, persistent memory, base docker-compose (PostgreSQL, MinIO, Keycloak, Redis, MailHog), Keycloak realm, init-db.sql, CI/CD, MkDocs
- [x] ADR-001 microservices · ADR-002 multi-tenancy
- [x] **auth-service**, **tenant-service**, **api-gateway**

### Phase 2 — Core Domain
- [x] **document-service** (radicación E/S/I, atomic numbering, attachments)
- [x] **storage-service** (MinIO, SHA-256, pre-signed URLs, per-tenant bucket)
- [x] **archive-service** (TRD, expedientes `open→closed→transferred`)

### Phase 3 — Workflows and Notifications
- [x] **workflow-service** (assignment/transfer, history, Redis events)
- [x] **archive-service** (radicado↔expediente link)
- [x] **notification-service** (SMTP, event consumer, history)

### Phase 4 — Quality and Integration
- [x] init-tenant, E2E, ADR-003 asyncpg, lint/type-check in CI, orchestrated health check

### Phase 5 — Community and Publication
- [x] Deployment/contribution guides, EN README, bilingual docs, AGPL v3, OpenAPI aggregation, GitHub Actions, GHCR, EN API reference

### Phase 6 — Advanced Features (scaffolding)
- [x] FondeCund TRD seed, Batch Documents/Expedientes, Full-Text Search, Workflow Rules Engine

---

## Beyond specDrive 📋 Planned

- [ ] Native mobile app (iOS/Android)
- [x] ~~Document signing~~ — native electronic signature done (E06); **PKI/XAdES digital signature** pending (signature-service)
- [ ] Corporate SSO integration (Entra ID, Okta, LDAP)
- [x] Frontend — SvelteKit scaffold in `frontend/` (ADR-011); full UI pending
- [x] ~~Webhooks API~~ — signed outgoing webhooks done (E11)
- [ ] Export expedientes to PDF/ZIP
- [x] ~~Reports and statistics~~ — radicado reports done (E09); advanced dashboard/indicators pending
