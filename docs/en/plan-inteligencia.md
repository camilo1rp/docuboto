# Intelligence plan (Docuboto 2.0)

> **Status: planned.** Nothing on this page is built. This is a condensed English version.
> The Spanish version of this page is canonical (switch with the language selector) and
> carries the per-service detail, file references and open questions. The architecture
> decision is [ADR-045](adr/ADR-045-pasarela-de-inteligencia-y-protocolo-ppi.md)
> (Proposed), written in Spanish like every ADR, and the protocol is PPI v1 (draft) in
> `docs/contratos/ppi/`. Updated as of 2026-10-27.

## Purpose

Docuboto 2.0 adds an **internal intelligence provider**: a separate program, under its own
license, that runs in its own containers inside the installation and talks to Docuboto
only through PPI. Docuboto stays the system of record and works fully with no provider.

| Phase | What the officer gets |
| --- | --- |
| **D1 — Read path** | Search and ask over record bodies and attachments, with citations |
| **D2 — Intake triage** | Suggestions for document type, due date, routing, related records and sender, accepted or rejected in the inbox |
| **D3 — Reply drafts** | A cited draft reply that becomes a Docuboto draft in the officer's name |

## Rules for every phase

1. **Only PÚBLICA content leaves.** RESERVADA and CLASIFICADA never reach a provider. One
   function decides and one choke point enforces it, re-reading the live level at send
   time.
2. **With the switch off, no calls.** With the ADR-037 flag off, or the entity on `local`,
   Docuboto makes no call to the provider.
3. **The officer executes what is accepted.** Every acceptance goes through the write gate
   as that person, and its audit entry carries the suggestion in `correlacion`. The
   provider has no credential for Docuboto's API.
4. **Code never names a provider.** The provider's name is data from its signed
   `capabilities` statement, and the image is set by a variable.

## D1 — Read path

- **knowledge-service becomes the pasarela.**
  - A provider port with `local` and `ppi` implementations.
  - A single egress choke point.
  - Its own consumer group on the document and workflow streams.
  - An append-only send ledger with periodic reconciliation.
  - An HMAC-signed internal `content` callback.
  - Delegated `/search` and `/rag` with a live re-check of every hit and citation, and a
    labelled `local` fallback.
  - A pseudonymous `on_behalf_of`.
- **document-service.** Internal routes for a record and for attachment bytes, which
  re-check the level and leave an access-trail entry. Two new outbox events: attachment
  added and record changed.
- **tenant-service.** `GET`/`PUT /api/v1/config/inteligencia` with a transmission
  declaration naming the provider and its subprocessors, plus a withdraw act.
- **`scripts/init_tenant.py`.** Creates one client-credentials Keycloak client per entity.
  It is not added to `ALLOWED_AZP`.
- **Frontend.** An "Ask" panel in the inbox detail drawer and an admin screen.
- **Infra.** A vendor-neutral `inteligencia` Compose profile on an internal network.
- **New permissions.** None.
- **Success criteria** match the provider's D1 gate:
  - cited answers or `insufficient_evidence`;
  - Docuboto keeps working with the provider stopped;
  - a recorded reconciliation shows nothing above PÚBLICA at the provider;
  - deletion is verified after reclassification, annulment and purge;
  - the installation runs from a published bundle;
  - no calls happen with the switch off;
  - human sign-off.

## D2 — Intake triage

- **The `sugerencias` inbox.**
  - Closed kinds.
  - Idempotent on (provider, ref).
  - States: pendiente, aceptada, rechazada, superada, expirada.
  - An explicit "none" with a reason is recorded for coverage.
- **Accepting a suggestion.** It needs three things: the flag, `PERM_CONOCIMIENTO` level 2,
  and the target operation's own permission. Acceptance mints a write-gate proposal in
  mcp-server, shown with `ProposalCard`. A signed suggestion header puts
  `correlacion.sugerencia` in the audit entry. Decisions go back to the provider through
  an outbox.
- **Due-date fixes, needed with or without a provider.**
  - A default term per document type.
  - The e-mail poller gets a tenant-service client.
  - An audited operation to set or correct a due date, under a new permission
    `PERM_VENCIMIENTO`.
  - workflow-service aligns the active step's due date.
- **Frontend.** Suggestion panels, inbox badges and an editable due date.
- **Success criteria** match the provider's D2 gate:
  - suggestions arrive within the agreed p95;
  - every acceptance shows in the audit chain with the officer and the suggestion's ID,
    provider and model;
  - no state change comes from the provider;
  - gold-set accuracy meets the agreed thresholds;
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
- Nothing above PÚBLICA reaches a provider, on any path, including the race where a record
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
