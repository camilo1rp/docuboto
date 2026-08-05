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

- [x] **Batch loading reports no result under any circumstance** (two independent defects that compound, both in `/admin/interoperabilidad`). (1) `createJobPoller` mutated a plain object that is never reassigned: Svelte 4 does not invalidate, and the panel stayed frozen on "Querying status…". (2) Both polling proxies requested `${API_V1}/batch/{jobId}/status`, the shape from **before** the gateway routing fix, which matches no prefix → permanent `404`. **Fixed (batch 1)**: polling moved to reassigned component-level `let` variables, the URLs to the real nested shape (`/batch/documents/{jobId}/status` and `/batch/expedientes/{jobId}/status`), the **four stale comments** claiming the gateway did not route `/api/v1/batch/` were **deleted** — that being the vector that produced the defect — and **URL contract tests** were added, the missing defense already paid for once with `FORWARDED_REQUEST_HEADERS`.
- [x] **Three `/admin` cards lead to a `403`** — the same pattern already fixed in `Colas`, which turned out not to be an isolated case. **Fixed (batch 1)** by checking all **16** cards against the real `require_permission` of their router, not just the three observed: `TRD / CCD` and `Metadatos` move to `USUA_PERM_TRD`, and `Interoperabilidad` — whose gate is **heterogeneous per tab** — now opens with `USUA_PERM_EXPEDIENTE || PERM_RADI` and filters each tab with its own permission, so it no longer hides from a registration operator who can use it. The other thirteen matched. A false comment in `metadatos/` claiming that the `document-service` routers did not gate by permission was corrected as well.
- [x] **Two screens built and never linked**: `/admin/preservacion` (784 lines) appeared neither in the card index nor in the sidebar — it was only reachable by typing the URL — and `/admin/seguridad/claves` was missing from the index, which presents itself as the complete administration map. **Fixed (batch 1)**: both added to `ADMIN_AREAS` with their permission verified against the router.
- [x] **The document batch-loading proxies never made it into version control** (a repository-hygiene finding, surfaced during batch 1 and **not** by the UX review). The `.gitignore` pattern `documentos/` — written for the `documentos/` folder at the repo root — carried no leading slash, so it also hid `frontend/src/routes/(app)/admin/interoperabilidad/api/lotes/documentos/`: the folder stayed out of the Phase 8 commit despite being documented as delivered, and on `main` the screen called routes that did not exist. **Fixed** by anchoring the pattern to `/documentos/`.
- [x] **AA contrast: white on `--op-primary`** (4.28:1) in **permanent** states, not just on `:hover`. **Fixed (batch 2)**: the three `Stepper`s (2FA, PKI credential, signing), the active turn in the signature chain and the filter chips in `/borradores` and `/envios` — which were literally **inverted**, with the dark tone on hover — now use `--op-primary-dark` (6.56:1), following what `Button.svelte` already got right. Four instances of the other failing pair were fixed as well: `--op-text-secondary` on `--op-primary-light`/`--op-success-bg`/`--op-error-bg` (~4.0:1).
- [x] **Wide tables unreachable** (WCAG 1.4.10 Reflow and 2.1.1 Keyboard). **Fixed (batch 2)**: `tabindex="0"` + `role="region"` + `aria-label` + `:focus-visible` on `DataTable` and `CatalogCrudPanel` — which propagate it to 8+ and 7 screens respectively — and on ~20 raw table wrappers; `bandeja-table`, the only one with `overflow: hidden` and no scrolling mechanism at all, gets its own wrapper with `overflow-x: auto`. The remaining `overflow: hidden` containers wrap a `DataTable` whose scrolling lives in the inner wrapper, so **no** tab stop was added to them: it would be mute and redundant. Every new `tabindex` trips `a11y-no-noninteractive-tabindex`, a rule that does not recognize WCAG technique SCR29; silenced with a justified `svelte-ignore` rather than left dangling.
- [x] **`/perfil`: seven field validation errors surface only as a floating toast**. **Fixed (batch 2)**: all seven are now associated with their field through `FormField`'s `error` prop (`aria-invalid`/`aria-describedby`), and the five OTP code fields gain `inputmode="numeric"` + `autocomplete="one-time-code"` — which required extending `FormField` with those two props, as they did not exist.
- [x] **`CatalogCrudPanel` applied `display: flex` to a `<td>`**, removing the cell from the table model in the accessibility tree. **Fixed (batch 2)** by moving the flex to a `<div>` inside the `<td>`; it propagates to the seven screens using the panel. The original finding also mentioned "four tables emitting one more cell than they have headers": a sweep of **all 28 raw tables** in the project, comparing `<th>` against `<td>` and including permission-conditional columns, **could not reproduce it** — it was most likely closed already when the shared pattern was adopted. Recorded as-is rather than forcing the count.
- [x] **AI suggestions appeared without being announced** to screen readers. **Fixed (batch 2)** with `aria-live="polite"` on the TRD series chips and on the antecedentes panel. The content itself was not touched: `ragInsert.js` remains the sole builder of the fragment and already meets the regulatory requirement.
- [ ] **There is no 2FA recovery code** in the backend (verified: 0 occurrences in `auth-service`). Anyone who loses their phone is left unable to revoke or renew their signature credential, and the screen does not say whom to contact. This is a product gap, not an interface one: it must not be papered over in the frontend. **Still open**, deliberately left out of batch 2.

- [x] **Contract root cause — `/auth/me` discarded the CRUD level.** The router resolved `{permission: crud_level}` and returned `list(perms.keys())`, so the store received a flat list and `can("USUA_PERM_EXPEDIENTE")` could not distinguish read from write, while the backends gate with `min_crud=3`. Both reviews reached this point independently and both refused to patch it screen by screen. **Fixed (batch 3)**, in two halves: `/auth/me` adds `permission_levels{}` **additively** — `permissions[]` is kept, so no coordinated deployment is needed — and the store gains `can(permission, minCrud)` with an optional `minCrud` and an explicit **fail-closed** rule: if the level does not arrive (a session predating the contract, an older backend), the effective level is `0`, never "I don't know ⇒ allow it".
  Applying it meant sweeping **every** write gate against the real `require_permission` of its router, and the result corrects the finding's own premise: the vast majority of mutations use `min_crud=1`, so **only four screens** needed level 3 (TVD and TRD in their convalidation circuit, deleting a tipo documental and a metadata element, and a new version of the preservation plan). Requiring level 3 where the backend does not would have been the same defect in the opposite direction. It also surfaced that in `/bandeja` the UI gate is **stricter** than the backend (return and visto bueno authorize by ownership of the step, with no `require_permission`): noted, not touched, because that is a different behavior change.
  Two collateral defects found and closed: `radicar/+page.svelte` evaluated `can()` inside a `$:` with no reactive dependency — `can()` uses `get()`, which is not reactive, so that line would never be re-evaluated after a session change within the same mount — and an em dash glued to an optional JSDoc parameter (`[minCrud] —`) broke the TypeScript parser. Also verified that `context/switch` does **not** alter permissions or levels, only `active_depe_id`.
