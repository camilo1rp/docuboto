# System screens

OrpycaMCP's frontend (`frontend/`, SvelteKit 2 + Svelte 4 SSR, E22) is the only
official presentation layer. No screen accesses data directly: every view has its
own `api/` subfolder acting as a **Backend-for-Frontend (BFF)** — it receives the
browser's action, attaches the JWT (stored in an httpOnly cookie, never visible to
client-side JS) and forwards the request to the `api-gateway`. See the full detail
of this architecture in [Architecture](architecture.md), "Authentication flow"
section.

The navigation and the actions shown on each screen mirror the user's real RBAC
(`PERM_RADI`, `USUA_PERM_EXPEDIENTE`, `USUA_PERM_ADMIN`, `PERM_RADI_SALIDA`,
`PERM_FIRMA`, `PERM_DLQ_ADMIN`, …), but that restriction is only defense-in-depth in
the UI — the backend always revalidates the permission and the clearance level
(RF-SEG-08) per request.

## Public screens (`(public)/`)

| Route | Purpose |
|---|---|
| `(public)/` | **Public landing page**: what OrpycaMCP is, the regulatory framework (Ley 594/2000, Acuerdo AGN 060/2001), capabilities, a comparison with Orfeo and the AGPL v3 license. If `locals.user` is populated (an active session), its `+page.server.js` **redirects 302 to `/dashboard`** — an authenticated user never sees it |

## Authentication

| Route | Purpose |
|---|---|
| `(public)/login` | Starts the OAuth2 Authorization Code + PKCE flow against Keycloak. **Split** composition: on mobile only the sign-in card; from 960px the brand panel appears. If Keycloak returns `?error=`, it shows a message mapped from a closed table in Spanish (never the IdP's raw string) with a "Retry" link — which breaks the redirect loop |
| `(public)/login/callback` | Receives the authorization code, exchanges it for tokens (server-side) and establishes the session |
| `(public)/logout` | Closes the local session and the Keycloak session |
| `/403` | Access-denied page when the user lacks the required permission |

## Operational screens (`(app)/`)

| Route | Screen | Purpose | Typical role | Main endpoints (via gateway) |
|---|---|---|---|---|
| `/dashboard` | Personal panel | **Four personal modules** visible without scrolling: 01 pending radicados, 02 recent activity, 03 alerts (open radicados by `due_date`, RF-RAD-04), 04 favorites. The **aggregate tenant metrics** live in a secondary collapsible section, closed by default and gated by `SGD_PERM_ESTADISTICA` | All authenticated users (metrics: `SGD_PERM_ESTADISTICA`) | `GET /api/v1/documents`, `GET /api/v1/reports/radicados` (metrics section only) |
| `/bandeja` | Inbox / case tray | View, process, return, annul, or reply to assigned radicados; request/grant vistos buenos (individual or chained); adjust the security level; view/set the final disposition (TRD) of the individual radicado, with explicit confirmation for the deletion action; the detail drawer includes a "Respuestas" section with the linked Salidas | Correspondence operator, dependencia officer | `GET/PATCH /api/v1/documents`, `/api/v1/workflows`, `/api/v1/documents/{id}/anulacion`, `/api/v1/documents/{id}/respuesta`, `GET /api/v1/documents/{id}/respuestas`, `GET/PATCH /api/v1/documents/{id}/disposition` |
| `/radicar` | Document registration | Register an incoming, outgoing, or internal radicado with its attachments; a "Suggest from precedents" button (RAG with citations, F6) for the body of Salida/Interno documents, and suggested tipo documental chips (never auto-applied) next to the classification selector | Front desk / correspondence operator (`PERM_RADI`, `PERM_RADI_SALIDA`) | `POST /api/v1/documents`, `POST /api/v1/storage/upload`, `POST /api/v1/knowledge/rag`, `POST /api/v1/knowledge/antecedentes` |
| `/borradores` | Drafts | Prepare a radicado before assigning it a number; approve it or register it definitively; a "Suggest from precedents" button (RAG with citations, F6) in the body editor | Drafting officer | `GET/POST /api/v1/documents/borradores`, "register" action, `POST /api/v1/knowledge/rag` |
| `/busqueda` | Document search | Three deep-linkable tabs (`?tab=`): **Exact** (PostgreSQL FTS full-text with filters), **Semantic** (documents similar in meaning, vector similarity) and **Precedents** (related matters, by text or by a verified pivot radicado) — F6. Semantic/Precedent results are always presented as "similar", never as an exact match; the access-permission trimming is invisible by design | Any user with query permission | `GET /api/v1/search`, `POST /api/v1/knowledge/search`, `POST /api/v1/knowledge/antecedentes`, `GET /api/v1/documents/by-tracking/{tracking}` |
| `/expedientes` | Expediente listing | View open/closed/transferred expedientes, create a new one | Archivist, officer with `USUA_PERM_EXPEDIENTE` | `GET/POST /api/v1/expedientes` |
| `/expedientes/[id]` | Expediente detail | Add/remove radicados, view attachments, close the expediente, download the signed electronic index XML, export a ZIP, start a transfer; view (read-only) the final disposition materialized at closure or, while still open, a preview of the assigned TRD series rule | Archivist | `GET/PATCH /api/v1/expedientes/{id}`, `GET /api/v1/expedientes/{id}/indice`, `GET /api/v1/expedientes/{id}/export`, `GET /api/v1/trd/{serie_id}` |
| `/archivo-fisico` | Physical archive | Manage locations (recursive addresses), conservation units, loans and their FUID | Central archive custodian | `GET/POST /api/v1/ubicaciones`, `/api/v1/unidades`, `/api/v1/prestamos` |
| `/transferencias` | Document transfers | Send/receive/reject transfers between the active archive and the central archive, generate the FUID | Archivist, dependencia head | `POST /api/v1/transferencias`, send/receive/reject actions |
| `/transferencias/[id]` | Transfer detail (acta as certificate) | View the acta de entrega presented as a certificate (what / between whom / when), the institutional XAdES seal, and sign it personally as elaborador/remitente/receptor — each person's role is derived from their identity, never chosen; verify seal and signatures on demand | Archivist, dependencia head with `USUA_PERM_EXPEDIENTE` (personal signing additionally requires `PERM_FIRMA` de facto, the backend gate) | `GET /api/v1/transferencias/{id}`, `.../acta`, `.../acta/verificacion`, `POST .../acta/firmar-personal`, `POST .../acta/firmar` |
| `/firmas` | Electronic signatures | Sign (individually or in batch) pending radicados/expedientes, reject a signature | Authorized signer (`PERM_FIRMA`) | `GET/POST /api/v1/signature`, `/api/v1/signature/cadena` |
| `/envios` | Postal shipments | Manage dispatches to the external postal operator, their delivery confirmation, and "Update tracking" (a PULL query against the operator, F5) which states explicitly when there is no live integration (`operador_conectado=false`) instead of faking a real query | Correspondence operator | `POST /api/v1/documents/{id}/envios`, `GET /api/v1/envios`, `GET /api/v1/envios/{id}/tracking`, incoming operator webhook |
| `/reportes` | Reports | Generate and export (CSV) records-management reports; "Classified index" tab (Ley 1712 art. 20) — a content-free register of reserved/classified radicados with their legal grounds, without exposing subject or content | Dependencia head, administrator (classified index: `PERM_RECLASIFICAR`) | `GET /api/v1/reports/radicados`, `GET /api/v1/reports/indice-reservado[.csv]` |
| `AssistantDock` (not a route) | Conversational assistant | Natural-language chat that queries the system (RAG with citations), translating the question into read-only MCP calls. It is a floating panel mounted in `AppLayout.svelte`, available from any screen: **there is no `/asistente` page**, only the BFF proxy `(app)/asistente/api/message` | Any authenticated user | `POST /api/v1/assistant/message` (via `mcp-server`) |
| `/admin` | Administration index | Card map of the administration panel (18 areas today). Each card declares the **real** `require_permission` of the router it consumes, not a generic `USUA_PERM_ADMIN` — hence `TRD / CCD`, `TVD` and `Metadatos` require `USUA_PERM_TRD`, `Colas` requires `PERM_DLQ_ADMIN`, `Preservación` requires `USUA_PERM_EXPEDIENTE`, and `Interoperabilidad` opens with `USUA_PERM_EXPEDIENTE` **or** `PERM_RADI`. A card the user cannot use is shown **disabled**, not hidden. Corrected in batch 1 of the UX review of 2026-08-02, which also linked the two screens that existed but appeared nowhere | Administrator (`USUA_PERM_ADMIN` for the panel; each card by its own permission) | — |
| `/admin/preservacion` | Digital preservation administration | View and update the (versioned) preservation plan and its history; operational visibility of electronic indexes pending XAdES sealing (with manual retry) and of WORM artefacts (index/acta/AIP) pending renewal of a Conservación Total retention. It does **not** offer manual AIP packaging or manual WORM protection of an arbitrary artefact: both need data (`documentos[]`/`file_id`) that only `archive-service` knows in the context of an expediente, and retention renewal is by design system-triggered only. It was not linked — reachable only by typing the URL — until batch 1 of the UX review of 2026-08-02, which added it to the `/admin` card index | Archivist, administrator (`USUA_PERM_EXPEDIENTE`; sealing retry also needs `PERM_FIRMA`) | `GET/PUT /api/v1/preservacion/plan`, `GET /api/v1/preservacion/plan/versions`, `GET /api/v1/expedientes/indices/pendientes-firma`, `GET /api/v1/expedientes/indices/pendientes-renovacion`, `POST /api/v1/expedientes/{id}/indice/firmar` |
| `/admin/plantillas` | Template administration | CRUD of reusable **body** templates for outbound documents, with a rich text editor. It is **not** a form builder: **metadata** templates (a JSON Schema per TRD series / tipo documental) are administered in `/admin/metadatos` | Administrator (`USUA_PERM_ADMIN`) | `GET/POST /api/v1/plantillas` |
| `/admin/trd` | TRD / CCD administration | Two tabs: TRD/CCD series in an **indented tree** (the real `parent_id` hierarchy, never a flat list) with create/edit (immutable code, append-only outside `borrador`); tipos documentales (third level), with create/edit/delete and a per-series filter. Convalidation circuit (approve → convalidate → register RUSD → repeal, plus return) via the shared `InstrumentLifecycleActions` component (ADR-026) — every irreversible act (convalidate, repeal) warns inside the confirmation dialog, before confirming | TRD administrator (`USUA_PERM_TRD`, the backend's real gate — more specific than `USUA_PERM_ADMIN`, which only controls the visibility of the `/admin` panel) | `GET/POST /api/v1/trd`, `PATCH /api/v1/trd/{id}`, `GET/POST /api/v1/trd/{code}/versiones`, `POST /api/v1/trd/{code}/versiones/{version}/{aprobar,devolver,convalidar,registrar-rusd,derogar}`, `GET/POST/PATCH/DELETE /api/v1/tipos-documentales` |
| `/admin/tvd` | TVD administration (accumulated fonds) | Structural mirror of `/admin/trd` (ADR-026): a tree of valuation groupings with its own columns — fonds/producing unit, extreme dates, instrument state — and **no** active-archive column (there is none for a TVD: an accumulated fonds is already in the central/historical archive). The creation form requires extreme dates and treats the **valuation rationale** as the main field, not a side note. Same convalidation circuit as TRD, same shared component | TRD administrator (`USUA_PERM_TRD` — ADR-026 deliberately reuses the TRD permission: same archival authority, same Comité) | `GET/POST /api/v1/tvd`, `PATCH /api/v1/tvd/{id}`, `GET/POST /api/v1/tvd/{code}/versiones`, `POST /api/v1/tvd/{code}/versiones/{version}/{aprobar,devolver,convalidar,registrar-rusd,derogar}` |
| `/admin/metadatos` | Metadata administration | Three tabs: **expediente** metadata templates (per TRD series, archive-service) and **document** ones (per tipo documental, document-service) — both create-only (immutable; the JSON Schema is edited as text with syntax validation before submitting; a new version = a new record); reusable metadata elements (full CRUD). Split with `/admin/trd`: TRD administers the classification, this screen administers the fields of those schemas | Administrator (`USUA_PERM_ADMIN` client-side; document-service's template/element routers **do not gate by permission in the backend today** — a domain finding documented in the code, not papered over in the frontend) | `GET/POST /api/v1/expediente-metadata/templates`, `GET/POST /api/v1/metadata/templates`, `GET/POST/PATCH/DELETE /api/v1/metadata/elements` |
| `/admin/catalogos` | Catalog administration | Master-detail: the tenant's reference catalogs on the left, the selected catalog's items on the right (full CRUD); on mobile it degrades to two navigable levels with a "Back" button, never to cramped columns | Administrator (`USUA_PERM_ADMIN` client-side; `catalogos.py` does not gate by permission in the backend today either) | `GET /api/v1/catalogos`, `GET/POST /api/v1/catalogos/{catalogo}`, `PATCH/DELETE /api/v1/catalogos/{catalogo}/{id}` |
| `/admin/parametros` | Parameter administration | Three sections: tenant parameters (key→free JSON value), the holiday calendar (individual entries + a Ley 51/1983 preload with no external call), and a business-day calculator that shows the **breakdown** of weekends/holidays subtracted in the range (the backend only returns the resulting date; the breakdown is reconstructed client-side from the same already-loaded holiday catalog) | Administrator (`USUA_PERM_ADMIN` client-side; `config.py` explicitly documents that the permission gate is pending) | `GET/PUT /api/v1/config/params[/{key}]`, `GET/POST/DELETE /api/v1/config/holidays`, `POST /api/v1/config/holidays/seed`, `GET /api/v1/business-days/calculate` |
| `/admin/grupos` | Group administration | List/create groups; a detail drawer with members, per-group permissions and clearance (RF-SEG-08). There is **no** group editing or deletion (the backend only has create+read). The backend **does not expose reads** of the memberships/permissions/clearance already assigned to a group either (only add/remove), which the drawer states explicitly — the actions apply immediately with no way to show "the current state" | Administrator (`USUA_PERM_ADMIN`) | `GET/POST /api/v1/auth/groups`, `GET /api/v1/auth/permissions`, `GET /api/v1/auth/security-levels`, `POST/DELETE /api/v1/auth/groups/{id}/members/{user_id}`, `PUT /api/v1/auth/groups/{id}/permissions/{permission_id}`, `PUT /api/v1/auth/groups/{id}/clearance` |
| `/admin/dependencias` | Dependencia administration | Tree view of the institutional org chart (`GET /dependencias/tree`); create, edit and activate/deactivate. Renaming or deactivating triggers an explicit confirmation modal (not a tooltip): dependencias are correlated **by name**, not by id/code, in `workflow_rules.assign_to_dept` and in in-flight `flow_steps` — both can be orphaned. It does **not** offer hard deletion (the backend has it, blocked when there are children, but this screen's scope is create/edit/deactivate) | Administrator (`USUA_PERM_ADMIN`) | `GET /api/v1/dependencias`, `GET /api/v1/dependencias/tree`, `POST /api/v1/dependencias`, `PATCH /api/v1/dependencias/{id}` |
| `/admin/pinar` | PINAR plan listing | MVP (Phase 7): list plans with a state filter, create a new version (always in `borrador`; reformulating = creating another version, history is preserved). There is **no** editing or deletion — a plan only changes through its life cycle in the workspace | Administrator (`USUA_PERM_ADMIN`) | `GET/POST /api/v1/pinar/planes` |
| `/admin/pinar/[id]` | PINAR plan workspace | An informational life-cycle `Stepper` (`borrador→aprobado→en_ejecucion→cerrado`, not clickable) + Summary and Dashboard tabs (progress by objective / by axis). Approve / move to execution / close actions with a confirmation `Modal` that warns about irreversibility BEFORE executing — approval **permanently freezes the plan's structural content** (an entry in the immutable `audit_log`); closing is terminal. **Out of MVP scope** (no UI, endpoints exist): critical aspects, prioritization, objectives, projects, follow-up, instruments, road map | Administrator (`USUA_PERM_ADMIN`; the detail/dashboard is read-only for any authenticated user) | `GET /api/v1/pinar/planes/{id}`, `POST .../aprobar`, `POST .../ejecutar`, `POST .../cerrar`, `GET .../tablero` |
| `/admin/seguridad/claves` | Administrative revocation of signing credentials | Lets an administrator revoke another user's personal PKI signing credential (E17/F4), requiring an exact confirmation phrase to be typed. It is **blind revocation**, and the screen says so: no endpoint lists the signing state per user (only `GET /auth/me/signing-key`, one's own), so the tenant's users are loaded as a wide page and filtered client-side, and the backend answers `404 no_signing_key` when the target has none. It was missing from the `/admin` card index — which presents itself as the complete administration map — until batch 1 of the UX review of 2026-08-02 | Administrator (`USUA_PERM_ADMIN`) | `GET /api/v1/auth/users`, `POST /api/v1/auth/admin/users/{user_id}/signing-key/revoke` |
| `/admin/interoperabilidad` | Interoperability and batch loading | Four tabs (Phase 8, last of the API↔UI drift closure): **Export** (interoperable ZIP package, downloaded client-side, with included/excluded counters by security level); **Import** (`Stepper` upload→validation→confirm→result; fixity/schema validation is atomic in the backend, and a `422` shows the EXACT file/checksum that failed, never a generic message); **Batch document loading** (up to 1000) and **Batch expediente loading** (up to 100), both with a confirmation modal stating the exact item count and 2s progress polling that distinguishes "could not query" from "no results yet". All four tabs work end to end; batch loading used to report no result (UX review 2026-08-02: the poller had kept the URL shape from before the gateway routing fix, and the progress panel was not reactive), **fixed in batch 1** together with a contract test that pins the URL shape | Administrator with `USUA_PERM_EXPEDIENTE` **or** registration operator with `PERM_RADI`: the gate is **heterogeneous per tab** — document batch loading requires `PERM_RADI` — so the screen opens with either one and each tab filters with its own permission, instead of offering what it will deny or hiding from someone who can use it | `POST /api/v1/export`, `POST /api/v1/import`, `POST/GET /api/v1/batch/documents[/{job_id}/status]`, `POST/GET /api/v1/batch/expedientes[/{job_id}/status]` |
| `/_kit` | UI component kit | Internal showcase of the Orpyca design system's components (development environment only, not production) | Developers | — |

## Global navigation

Present on **every** `(app)/` screen, provided by `AppLayout` and `SidebarNav`:

- **Global search** (`role="search"` in the top bar): navigates to `/busqueda?q=…`,
  the same full-text search view with its advanced filters. Keyboard shortcut
  <kbd>/</kbd>, suppressed when focus is already in an editable field. On narrow
  screens it expands from an icon. Because the term travels in the URL, every
  search is linkable and shareable.
- **Favorites**: every sidebar entry has a pin/unpin control (`aria-pressed`, never
  signaled by color alone). Pinned items head the menu and feed module 04 of the
  `/dashboard`. They are persisted in `localStorage` **namespaced per tenant +
  user** (`lib/stores/favorites.js`, with an SSR guard). The store does **not**
  filter by itself: it receives the already permission-filtered list from
  `lib/utils/navItems.js`, so a favorite whose permission is withdrawn disappears
  on its own. The `NAV_ITEMS` catalog and its RBAC filter are the **single source**
  shared by `SidebarNav` and the `/dashboard`.

## Design notes

- **Orpyca design system v1.0**: all reusable components (`DataTable`, `Drawer`,
  `Modal`, `StatusChip`, `FormField`, `Button`, `Loader`, `Stepper`, `Card`,
  `EmptyState`, `Toast`, `UserPicker`) live in `frontend/src/lib/components/ui/`
  and use `--op-*` design tokens (colors/spacing/typography/radii/shadows/motion),
  never loose hex values (ADR-020). The single source is `src/styles/_tokens.scss`.
- **Contrast and use of the brand green**: the v1.0 primary (`--op-primary`,
  `#2A8C3A`) measures **4.28:1** on white, below the 4.5:1 WCAG AA requires for
  normal text. The token is therefore **split by role**: `--op-primary` only for
  **non-textual** elements (borders, icons, focus rings, ≥3:1 threshold) and
  `--op-primary-dark` (`#1C6B33`, **6.56:1**) for all **text** — including Bulma's
  `$primary`/`$link` — or the verified `--op-*-fg` pairs. The raw semantic colors
  (`--op-warning`, `--op-error`, …) are **border/icon** colors; their text version
  is `--op-*-fg`. `--op-accent` (`#8DBF3C`, 2.18:1) is **never** used as text on a
  light background.
- **Typography**: Space Grotesk (display, headings, figures) + Public Sans (UI and
  body), **self-hosted** via `@fontsource` — no external CDN, for sovereignty and
  so that users' browsing is not leaked to a third party.
- **Motion**: `_tokens.scss` declares a global `prefers-reduced-motion` rule that
  neutralizes every transition and animation site-wide; no screen needs to repeat
  it.
- **`DataTable` is the system's table**: it exposes eight capabilities **opt-in per
  prop** (per-column filters, sorting with `aria-sort`, CSV export, configurable
  columns, saved views, multiple selection, render-level virtualization and
  in-cell editing) with the simple behavior as the default. Virtualization uses
  `content-visibility` rather than removing rows from the DOM: less aggressive, but
  it does not break the native `<table>` semantics for screen readers. It is used
  by `/busqueda`, `/expedientes`, `/firmas`, `/envios` and the four panels of
  `/reportes`; `/bandeja` and `/archivo-fisico` still use their own table (see the
  [Roadmap](roadmap.md)).
- **Rich-text editing** (replies, observations): uses TipTap 3, with the HTML
  sanitized **server-side** before saving and before rendering — raw user HTML is
  never interpolated.
- **CSV export** from the browser neutralizes formula injection (prepends `'` to
  cells starting with `= + - @`).
- There are no screenshots in this documentation — the tables above describe the
  actual implemented functionality; to see it running, start the platform (see
  [Getting Started](getting-started.md)) and navigate to `http://localhost:19300`.

## See also

- [Architecture](architecture.md) — how each screen connects to the microservices
  through the gateway.
- [API Reference](api.md) — the full contract for each endpoint listed above.
