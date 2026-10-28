# Intelligence plan

> **Status: planned.** Nothing on this page is built. This is a condensed English version.
> The Spanish version of this page is canonical (switch with the language selector) and
> carries the per-service detail, file references and open questions. The architecture
> decision is [ADR-045](adr/ADR-045-pasarela-de-inteligencia-y-protocolo-ppi.md)
> (Proposed), written in Spanish like every ADR, and the protocol is PPI v1 (draft) in
> `docs/contratos/ppi/`. Updated as of 2026-10-27, with the ADR-045 revision that lets each
> entity choose which levels leave. [ADR-046](adr/ADR-046-conocimiento-sin-modelos.md)
> (Proposed, 2026-10-28) proposes that knowledge-service call no models: `local` becomes
> document-service full-text search, with no generation. Where this plan says "local index",
> read that fallback.

## Purpose

Docuboto evolves to add an **internal intelligence provider**: a separate program, under
its own license, that runs in its own containers inside the installation, under contract
with the entity, and talks to Docuboto only through PPI. Docuboto stays the system of record and works fully with no provider.

| Phase | What the officer gets |
| --- | --- |
| **D0 — Remove the local model layer** | Nothing new: search and related records keep working, now through full-text search; "suggest text" hides until a provider exists |
| **D1 — Read path** | Search and ask over record bodies and attachments, with citations |
| **D2 — Intake triage** | Suggestions for document type, due date, routing, related records and sender, accepted or rejected in the inbox |
| **D3 — Reply drafts** | A cited draft reply that becomes a Docuboto draft in the officer's name |

**Pilot start (2026-10-28).** D1 and D2 go live with PÚBLICA only. RESERVADA and
CLASIFICADA are enabled later through the existing admin act, once counsel (ADR-045
question 8) and the provider's security review sign off. No phase waits on this.

## Rules for every phase

1. **Only the levels the entity enables leave.** By default, only PÚBLICA. RESERVADA and
   CLASIFICADA leave only after the administrator enables that level, with a declaration
   naming the provider's model route for it. A record at a level that isn't enabled never
   reaches the provider. One function decides and one choke point enforces it, re-reading
   the live level and the enabled levels at send time.
2. **With the switch off, no calls.** With the ADR-037 flag off, or the entity on `local`,
   Docuboto makes no call to the provider.
3. **The officer executes what is accepted.** Every acceptance goes through the write gate
   as that person, and its audit entry carries the suggestion in `correlacion`. The
   provider has no credential for Docuboto's API.
4. **Code never names a provider.** The provider's name is data from its signed
   `capabilities` statement, and its URL is set by a variable (`PPI_PROVIDER_URL`).

## D0 — Remove the local model layer

Runs before D1 and needs no provider ([ADR-046](adr/ADR-046-conocimiento-sin-modelos.md)).
It only removes code, and it stops today's default of sending content to ollama.com.

- **knowledge-service.** `/search` and `/antecedentes` query document-service's
  `GET /api/v1/search` with the user's token and keep their response shape, labeled "local
  index". `/rag` returns 503 `rag_unavailable`. A new `GET /api/v1/knowledge/availability`
  says whether generation exists for the entity. Local generation, embeddings, the
  `knowledge-ingest` consumer, level reconciliation, re-embedding and the ML image layer are
  removed. A migration drops `knowledge_chunk`, `kb_suggestions` and the `vector` extension;
  there are no installations with data to keep.
- **mcp-server.** The conversational assistant is retired (ADR-046 §6), with `stt.py`,
  `assistant_model` and the `anthropic` dependency. The MCP tool list, its transports and
  the write gate stay.
- **Frontend.** "Suggest text" shows only when `availability` says generation exists.
  `AssistantDock.svelte` and `VoiceInput.svelte` are removed.
- **Infra.** Compose stops passing the `AI_*`, `EMBEDDING_*`, `OLLAMA_*` and
  `ANTHROPIC_API_KEY` variables, and `whisper-service` and the `assistant` profile go.
- **Guard test.** Neither knowledge-service nor mcp-server imports a model SDK or reads a
  model variable.
- **Success criteria:** search returns the same records as the search screen for the same
  user, never one outside their clearance; `/rag` returns 503 and the button hides; the
  service starts with outbound network cut; the three MCP knowledge tools keep working.

## D1 — Read path

- **knowledge-service becomes the pasarela.**
  - A provider port with `local` and `ppi` implementations.
  - A single egress choke point.
  - Its own consumer group on the document and workflow streams.
  - An append-only send ledger with periodic reconciliation.
  - An HMAC-signed internal `content` callback.
  - Delegated `/search` and `/rag` with a live re-check of every hit and citation, and a
    labelled `local` fallback. Timeouts proposed to the provider: search 3 s (p95 2 s), ask
    30 s (p95 15 s), draft 5 min.
  - A pseudonymous `on_behalf_of`, with the officer's real clearance capped at the highest
    enabled level.
- **document-service.** Internal routes for a record and for attachment bytes, which
  re-check the level and leave an access-trail entry. Two new outbox events: attachment
  added and record changed.
- **tenant-service.** `GET`/`PUT /api/v1/config/intelligence` with a transmission
  declaration naming the provider and its subprocessors, plus a withdraw act. The same
  `PUT` turns `intelligence_send_metadata` on or off (default off): when on, metadata
  template fields go out flattened in PPI 1.2's `record.metadata`. Switching
  to `ppi` enables PÚBLICA. `PUT /api/v1/config/intelligence/levels` enables RESERVADA or
  CLASIFICADA one level at a time, with a declaration naming that level's model route,
  models and subprocessors from `capabilities`. Disabling a level is immediate and orders
  deletion of what was sent at that level.
- **`scripts/init_tenant.py`.** Creates one client-credentials Keycloak client per entity.
  It is not added to `ALLOWED_AZP`.
- **Frontend.** An "Ask" panel in the inbox detail drawer and an admin screen. On `local`,
  the admin screen shows how many records the provider still holds, and the switch to
  `local` offers "withdraw the data too" as a checkbox.
- **Infra.** A vendor-neutral `intelligence` Compose profile that starts no provider
  service. It switches knowledge-service to `ppi` and attaches it to the external
  `ppi` network, created once per host with `--internal`. The provider is its own
  Compose project with its own release.
- **New permissions.** None.
- **Success criteria** match the provider's D1 gate:
  - cited answers or `insufficient_evidence`;
  - Docuboto keeps working with the provider stopped;
  - a recorded reconciliation shows nothing outside the enabled levels at the provider;
  - deletion is verified after reclassification to a level that isn't enabled, annulment,
    purge and disabling a level;
  - each enabled level's model route matches the signed `capabilities`;
  - the installation runs from a published bundle;
  - no calls happen with the switch off;
  - human sign-off.

## D2 — Intake triage

- **The `suggestions` inbox.**
  - Closed kinds.
  - Idempotent on (provider, ref).
  - States: `pending`, `accepted`, `rejected`, `superseded`, `expired`.
  - An explicit "none" with a reason is recorded for coverage.
- **Accepting a suggestion.** It needs three things: the flag, `PERM_CONOCIMIENTO` level 2,
  and the target operation's own permission. Acceptance mints a write-gate proposal in
  mcp-server, shown with `ProposalCard`. The card opens with the suggested values filled in
  and editable, and the decision goes back as `accepted` with `applied_arguments`. A signed suggestion header puts
  `correlacion.suggestion` in the audit entry. Decisions go back to the provider through
  an outbox.
- **Record-to-record links.** Accepting a related-records suggestion creates a symmetric
  `related` link between the two records (new `record_links` table and
  `link_related_records` operation, `PERM_RADI` level 2, clearance on both records). A
  suggestion never files a record into an expediente; the record detail shows a "related
  records" section where the officer can do that by hand. Links go to the provider in PPI
  `record.links`.
- **Due-date fixes, needed with or without a provider.**
  - A default term per document type.
  - The e-mail poller gets a tenant-service client.
  - An audited operation to set or correct a due date, under a new permission
    `PERM_DUE_DATE`. "Not your own": the record's current assignee can't extend its due
    date, even with the permission; a colleague or supervisor does it, with a reason.
    Shortening is always allowed, and an extension can't exceed double the original
    term (Ley 1755 de 2015, art. 14, to confirm). No group gets the permission
    automatically: the admin assigns it, and the admin screen warns when no group has it.
  - workflow-service aligns the active step's due date.
- **Frontend.** Suggestion panels, inbox badges and an editable due date.
- **Success criteria** match the provider's D2 gate:
  - suggestions, or an explicit "none", arrive within p95 10 minutes of registration and
    99% within 60 minutes, for attachments up to 50 pages in total;
  - every acceptance shows in the audit chain with the officer and the suggestion's ID,
    provider and model;
  - no state change comes from the provider;
  - gold-set accuracy, measured offline before D2 goes live, meets the proposed
    thresholds: document type top-1/top-3 ≥ 80%/95%, due date exact ≥ 95%, routing
    top-1/top-3 ≥ 70%/90%, related records precision ≥ 70%, party match precision ≥ 95%.
    A kind that misses isn't switched on: the provider doesn't emit it, so Docuboto needs
    no per-kind switch;
  - at least 95% coverage (suggestions or an explicit "none");
  - turning intelligence off stops all calls;
  - human sign-off.

## D3 — Reply drafts

- An officer asks for a draft. The provider's `run` returns a `draft` recommendation.
- Accepting it creates a Docuboto draft (`crear_borrador` with `radicado_padre_id`, which
  the MCP tool must start exposing) in the officer's name. The draft then follows the
  normal review, approval and signature path.
- The suggested body is rendered as text, never as HTML.

## Cross-cutting guard tests

- No provider name appears in code. The denylist is kept as SHA-256 digests, so the test
  itself names no one.
- Nothing at a level the entity hasn't enabled reaches a provider, on any path, including the race where a record
  is reclassified between the event and the send.
- With the switch off, no calls or egress happen.
- Every acceptance is audited with its suggestion ID.

## Hardening found while reading the code

Verified by reading at commit `7510257`, not run and not fixed:

1. The e-mail automatic mode calls `radicar(..., datos={})`, which fails the minimum-data
   check for every message.
2. Workflow `_publish` swallows errors.
3. The auto-assignment audit entry doesn't record the rule.
4. The `test-unit-shared` CI job doesn't install `fastapi`.
5. `integraciones-externas.md` §5 still says there is no per-entity opt-in.
6. knowledge-service's read of `radicados` isn't declared in ADR-043 §5.
