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

---

## Frontend (E22) pending items

### UX review of the API↔UI drift closure (2026-08-02)

The ~20 screens built during the API↔UI drift closure (Phases 0–8) were implemented **without going through the UX/accessibility reviewer**, the last link in the project's agent chain. The review was done afterwards, in two parallel passes (the `/admin` panel and the operational screens). Result: a solid base — no unsanitized `{@html}`, no client-side calls to the gateway (rule D-05 intact), `Modal`/`Drawer` with correct focus trapping and restoration, confirmation proportional to the damage on irreversible acts — with these pending items:

- [ ] **Batch loading reports no result under any circumstance** (two independent defects that compound, both in `/admin/interoperabilidad`). (1) `createJobPoller` (`+page.svelte:239`) mutates a plain object that is never reassigned: Svelte 4 does not invalidate, and the panel stays frozen on "Querying status…". (2) Both polling proxies request `${API_V1}/batch/{jobId}/status`, the shape from **before** the gateway routing fix, which matches no prefix → permanent `404`. The administrator cannot tell whether 1000 radicados were created or everything failed. It also deserves a URL contract test: this is the same failure mode that already appeared with `FORWARDED_REQUEST_HEADERS`.
- [ ] **Three `/admin` cards lead to a `403`** — the same pattern already fixed in `Colas`, which turned out not to be an isolated case. `TRD / CCD` is advertised with `USUA_PERM_ADMIN` when the real gate is `USUA_PERM_TRD`; `Metadatos` offers "New expediente template" with `USUA_PERM_ADMIN` when it requires `USUA_PERM_TRD`; and `Interoperabilidad` gates **the whole** screen with `USUA_PERM_EXPEDIENTE` even though document batch loading requires `PERM_RADI` — a double error, because it also **hides the entire screen from a registration operator who could use it**.
- [ ] **Two screens built and never linked**: `/admin/preservacion` (784 lines) appears neither in the card index nor in the sidebar — it is only reachable by typing the URL — and `/admin/seguridad/claves` is missing from the index, which presents itself as the complete administration map.
- [ ] **AA contrast: white on `--op-primary`** (4.28:1) in **permanent** states, not just on `:hover` — the current-step digit of `Stepper` (all three: 2FA, PKI credential and signing), the active turn number in the signature chain, and the active filter chips in `/borradores` and `/envios`. In several blocks it is **inverted**: `--op-primary-dark` (6.56:1) on hover and the light one at rest. `Button.svelte:148` already got this right; the pages drifted from their own design system.
- [ ] **Wide tables unreachable**: six containers use `overflow: hidden` with `nowrap` cells, so on mobile the right-hand columns (Status and **Actions**) are cut off with no scrollbar (WCAG 1.4.10 Reflow). And those that do scroll carry no `tabindex="0"`, so no keyboard-only user can scroll them (WCAG 2.1.1) — including `DataTable.svelte` and `CatalogCrudPanel.svelte`, which propagate it to every screen that uses them.
- [ ] **`/perfil`: seven field validation errors surface only as a floating toast**, never associated with the field, even though `FormField` already supports `error` with `aria-invalid`/`aria-describedby` and the rest of the project uses it. A screen-reader user cannot tell which field failed — on the screen that governs the electronic signature credential. The OTP code fields also declare neither `inputmode="numeric"` nor `autocomplete="one-time-code"`.
- [ ] **Four tables emit one more cell than they have headers** (the actions column has no `<th>`), and `CatalogCrudPanel` applies `display: flex` to a `<td>`, which removes the cell from the table model in the accessibility tree. It affects the eight screens using the panel: worth closing in the shared component before it is copied into a ninth.
- [ ] **AI suggestions appear without being announced** to screen readers (TRD series chips after a debounce, the antecedentes panel when it opens). The content itself **does** meet the regulatory requirement: `ragInsert.js` is the sole builder of the fragment and always prepends the AI-generation notice plus the sources, inserting without citations is blocked, and the TRD series is never auto-applied.
- [ ] **There is no 2FA recovery code** in the backend (verified: 0 occurrences in `auth-service`). Anyone who loses their phone is left unable to revoke or renew their signature credential, and the screen does not say whom to contact. This is a product gap, not an interface one: it must not be papered over in the frontend.

- [ ] **Contract root cause — `/auth/me` discards the CRUD level.** `auth-service/app/routers/auth.py:226` resolves `{permission: crud_level}` and returns `list(perms.keys())`, so the store receives a flat list and `can("USUA_PERM_EXPEDIENTE")` cannot distinguish read from write — while the backends do gate with `min_crud=3`. Consequence: Close / Transfer / Exclude / Sign acta are offered to users who hold the permission in read mode, and they get a `403`. **Both reviews reached this point independently and both refused to patch it screen by screen**: the fix is to return the level in `/auth/me` and add a `can(perm, level)` to the store. It is an architectural decision and it touches the backend.
