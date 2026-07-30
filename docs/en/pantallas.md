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

## Authentication

| Route | Purpose |
|---|---|
| `(public)/login` | Starts the OAuth2 Authorization Code + PKCE flow against Keycloak |
| `(public)/login/callback` | Receives the authorization code, exchanges it for tokens (server-side) and establishes the session |
| `(public)/logout` | Closes the local session and the Keycloak session |
| `/403` | Access-denied page when the user lacks the required permission |

## Operational screens (`(app)/`)

| Route | Screen | Purpose | Typical role | Main endpoints (via gateway) |
|---|---|---|---|---|
| `/dashboard` | Main panel | Summary of the user's/tenant's activity: pending items, alerts, shortcuts | All authenticated users | `GET /api/v1/documents`, `GET /api/v1/workflows` (summary) |
| `/bandeja` | Inbox / case tray | View, process, return, annul, or reply to assigned radicados; request/grant sign-offs (individual or chained); adjust security level | Correspondence operator, dependencia officer | `GET/PATCH /api/v1/documents`, `/api/v1/workflows`, `/api/v1/documents/{id}/anulacion`, `/api/v1/documents/{id}/respuesta` |
| `/radicar` | Document registration | Register an incoming, outgoing, or internal radicado with its attachments | Front desk / correspondence operator (`PERM_RADI`, `PERM_RADI_SALIDA`) | `POST /api/v1/documents`, `POST /api/v1/storage/upload` |
| `/borradores` | Drafts | Prepare a radicado before assigning it a number; approve it or register it definitively | Drafting officer | `GET/POST /api/v1/documents/borradores`, "register" action |
| `/busqueda` | Document search | Full-text search (PostgreSQL FTS) over radicados and expedientes, with filters | Any user with query permission | `GET /api/v1/search` |
| `/expedientes` | Expediente listing | View open/closed/transferred expedientes, create a new one | Archivist, officer with `USUA_PERM_EXPEDIENTE` | `GET/POST /api/v1/expedientes` |
| `/expedientes/[id]` | Expediente detail | Add/remove radicados, view attachments, close the expediente, download the signed electronic index XML, export ZIP, start a transfer | Archivist | `GET/PATCH /api/v1/expedientes/{id}`, `GET /api/v1/expedientes/{id}/indice`, `GET /api/v1/expedientes/{id}/export` |
| `/archivo-fisico` | Physical archive | Manage locations (recursive addresses), conservation units, loans and their FUID | Central archive custodian | `GET/POST /api/v1/ubicaciones`, `/api/v1/unidades`, `/api/v1/prestamos` |
| `/transferencias` | Document transfers | Send/receive/reject transfers between the active archive and the central archive, generate the FUID | Archivist, dependencia head | `POST /api/v1/transferencias`, send/receive/reject actions |
| `/firmas` | Electronic signatures | Sign (individually or in batch) pending radicados/expedientes, reject signature | Authorized signer (`PERM_FIRMA`) | `GET/POST /api/v1/signature`, `/api/v1/signature/cadena` |
| `/envios` | Postal shipments | Manage dispatches to the external postal operator and delivery confirmation | Correspondence operator | `POST /api/v1/documents` (postal), incoming operator webhook |
| `/reportes` | Reports | Generate and export (CSV) records-management reports | Dependencia head, administrator | `GET /api/v1/reports` |
| `AssistantDock` (not a route) | Conversational assistant | Natural-language chat that queries the system (RAG with citations), translating the question into read-only MCP calls. It is a floating panel mounted in `AppLayout.svelte`, available from any screen: **there is no `/asistente` page**, only the BFF proxy `(app)/asistente/api/message` | Any authenticated user | `POST /api/v1/assistant/message` (via `mcp-server`) |
| `/admin/plantillas` | Template administration | CRUD of reusable **body** templates for outbound documents, with a rich text editor. It is **not** a form builder: **metadata** templates are only consumed (from `/radicar` and `/expedientes`) and have no administration UI yet — see RF-ADM-06 in the roadmap | Administrator (`USUA_PERM_ADMIN`) | `GET/POST /api/v1/plantillas` |
| `/_kit` | UI component kit | Internal showcase of the Orpyca design system's components (development environment only, not production) | Developers | — |

## Design notes

- **Orpyca design system**: all reusable components (`DataTable`, `Drawer`,
  `Modal`, `StatusChip`, `FormField`, `Button`, `Loader`, `Stepper`) live in
  `frontend/src/lib/components/ui/` and use `--op-*` design tokens (colors/spacing),
  never loose hex values (ADR-020).
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
