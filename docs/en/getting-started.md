# Getting Started

## Requirements

- Docker 24+ and Docker Compose v2
- Hardware: the only figure is in [Deployment](deployment.md#minimum-hardware)
- Free ports: 19000, 19180, 19300, 15432, 19900, 16379

> The app is one process (ADR-047): it listens on 8000 inside the `orpycamcp-net` Docker
> network and is published on `127.0.0.1:19000`. There's no `api-gateway` anymore (19080 is
> unused) and no port per service. The ports **published** to the host (19xxx / 15432 /
> 16379 range) avoid clashing with other local dev stacks. See the full table in
> [Architecture](architecture.md#the-process-and-its-containers).

## Local Installation (development)

```bash
# 1. Clone the repository
git clone https://gitlab.com/orpyca/orfeo_mcp.git
cd orfeo_mcp

# 2. Start the full infrastructure
docker compose --profile dev up -d

# 3. Verify the containers are running
#    (migrate-docuboto exits after applying the migrations; app waits for it)
docker compose ps
```

To join a local provider over PPI, add `-f docker-compose.intelligence.yml` (it only adds `app` to the `ppi` network) and set `PPI_PROVIDER_URL`: with it set, the ingestion consumer starts in the app's lifespan; empty, no call leaves. The start order, the `ppi` network, and the first **Inteligencia** check live in the provider's `docs/runbooks/local-dev.md` (section "Join a local Docuboto").

## Tests

The app's tests live in `services/docuboto/tests/<domain>/` (one folder per domain package,
plus `tests/docuboto/` for the composition root). From `services/docuboto`, with the
environment's `python`/`pytest` on your PATH:

```bash
../../scripts/test-app.sh unit              # each tests/<domain>/, one at a time
../../scripts/test-app.sh unit archive      # only those domains
../../scripts/test-app.sh integration       # each tests/<domain>/integration/
```

Each domain runs in **its own pytest invocation**: domain `conftest` files set their own
environment, and in a single session the collection order would decide the result. The script
derives the domain list from the tree. `integration` needs `DATABASE_URL`, `REDIS_URL`, and
`RUN_INTEGRATION=1`, and it recreates the database and flushes Redis before each domain.

To run locally what `.github/workflows/ci.yml` runs:

```bash
./scripts/ci-local.sh lint shared smoke     # the jobs you name
./scripts/ci-local.sh all                   # every job
```

`scripts/ci-local.sh` mirrors `ci.yml` (change one, change the other); `integration` starts a
throwaway PostgreSQL and Redis. `./scripts/lint.sh` runs ruff and mypy over the whole app.

## Access URLs

| Service | URL | Dev credentials |
|---|---|---|
| App API (docs) | http://localhost:19000/docs | — |
| Frontend (SvelteKit) | http://localhost:19300 | — |
| Object store (S3 API) | http://localhost:19900 | orpycamcp / orpycamcp_dev |

> The object store is **RustFS** since ADR-027 (MinIO archived its open-source project and
> pulled its images). It has **no web console** — port 19901 is gone: use `aws-cli` against
> `http://localhost:19900` to inspect buckets.

| PostgreSQL | localhost:15432 | orpycamcp / orpycamcp |
| Mailpit (email dev) | http://localhost:8025 | — |

## First Document Registration

A `radicado` is an official document registered with a unique, immutable tracking number.

```bash
# 1. Obtain an access token (against the app, port 19000)
TOKEN=$(curl -s -X POST 'http://localhost:19000/api/v1/auth/login' \
  -H 'Content-Type: application/json' \
  -d '{"username": "operador", "password": "orpycamcp_dev", "tenant_slug": "demo"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# 2. Register an incoming document
curl -X POST 'http://localhost:19000/api/v1/documents' \
  -H "Authorization: Bearer $TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{
    "subject": "Solicitud de información pública",
    "document_type": "entrada",
    "sender_name": "Ciudadano Juan Pérez",
    "sender_email": "juan@ejemplo.com"
  }'
```

The response includes the assigned `radicado` (tracking) number (e.g.: `2024-DEMO-E-000001`).

## Add a tenant (institution)

A `tenant` is the institution an installation serves. One installation serves one entity (ADR-047 §3).

Onboarding the institution is a platform operation with no HTTP route
(see [Multi-tenancy](multi-tenancy.md)). Use the provisioning script:

```bash
./scripts/init-tenant.sh mi-entidad "Mi Entidad Pública" MENT
```

This provisions the `tenant_mi_entidad` schema (each tenant's isolated database schema, named `tenant_{slug}`) in PostgreSQL (the slug takes lowercase letters, digits and hyphens; the hyphen becomes an underscore in the schema) and applies the migrations. The entity has no lifecycle states: once it exists with its schema, it's served.

## Load initial TRD (seed)

OrpycaMCP includes a reusable script to seed a tenant's TRD (`Tabla de Retención Documental` — document retention schedule) from a JSON file. The project ships the FondeCund TRD as a reference (150+ series).

**1. Export your institution's TRD to a compatible JSON:**

```json
{
  "entidad": "Mi Entidad Pública",
  "version": "2024-01-01",
  "dependencias": [
    {
      "nombre": "Departamento",
      "series": [
        {
          "codigo": 100,
          "nombre": "SERIE DOCUMENTAL",
          "retencion_gestion": 5,
          "retencion_central": 10,
          "disposicion_final": "Conservación total",
          "subseries": []
        }
      ]
    }
  ]
}
```

The Spanish final-disposition values are mapped to OrpycaMCP codes (`conserve`, `eliminate`, `transfer`).

**2. Generate the migration SQL for your tenant:**

```bash
python3 scripts/migrate_trd_from_fondecund.py \
  mi_entidad_trd.json \
  --tenant-slug mi_entidad \
  --output services/docuboto/migrations/seeds/trd_mi_entidad.sql
```

**3. Apply the migration in the tenant's schema:**

```bash
docker compose exec postgres psql -U orpycamcp -d orpycamcp_db \
  -c "SET search_path TO tenant_mi_entidad;" \
  -f services/docuboto/migrations/seeds/trd_mi_entidad.sql
```

**Retention and producing department (C-4).** The script requires `retencion_gestion` **and** `retencion_central` in the source JSON (it stops naming the series that lacks them: it does not invent retention times). A series seeded this way is `migrada` or `borrador` and has **no producing department**; for it to govern and appear in the classification scheme, open a new version with `dependencias_productoras` (`POST /api/v1/trd/{code}/versiones`), approve it and validate it.

## The documentary classification scheme (C-4)

The scheme is **not captured**: it is derived from the organigram and the TRD. Steps to have it:

1. **Organigram** (`/admin/dependencias`): create the dependencias with their hierarchy (the root is a *section*, each level below a *subsection*). The organigram admits no cycles (`409 dependencia_ciclo`).
2. **Entity name** (`/admin/parametros`, `entidad_nombre`): it is the *fonds*. If it is empty the scheme warns instead of inventing it.
3. **TRD** (`/admin/trd`): when creating a series declare its **two retention phases** (mandatory; the system computes the total) and its **producing department(s)**. Without a producer the series cannot be approved or validated (`409 trd_serie_sin_productora`). To add or change the producer of an already validated series a new version must be opened.
4. **Reading it**: the «Classification scheme» tab of `/admin/trd` (or `GET /api/v1/trd/cuadro`) shows fonds > section > subsection > series > subseries, the **warnings** (inactive producer, organigram reorganised since validation) and the series that cannot be placed.

**Publishing ex officio** (Law 1712, art. 12) now requires the radicado's *document type* to be marked **"Publishable"** in `/admin/trd` → Document types (needs TRD level 3). It is a different flag from "Admits public window": being received through the window does not imply being disclosed.

## More end-to-end usage examples

These examples assume you already have `$TOKEN` from the previous step. The full
contract for each endpoint is in the [API Reference](api.md); the business detail
behind each entity is in [Document domain](domain.md).

### Create an expediente and link a radicado

```bash
# 1. Create the expediente (requires a TRD configured for the series)
curl -X POST 'http://localhost:19000/api/v1/expedientes' \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"nombre": "Contract 2024-001 — Office supplies", "serie_id": "uuid-serie"}'
# Response: { "id": "uuid-expediente", "estado": "open", ... }

# 2. Link the radicado created earlier
curl -X POST 'http://localhost:19000/api/v1/expedientes/uuid-expediente/radicados' \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"radicado_id": "uuid-radicado"}'

# 3. Close the expediente (generates and signs the electronic index, see Lifecycle)
curl -X PATCH 'http://localhost:19000/api/v1/expedientes/uuid-expediente' \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"estado": "closed"}'
```

### Electronically sign a radicado

**There is no one-shot "sign".** Signing means resolving a turn of a circuit, and the
circuit is opened first — even when a single person signs. That is deliberate: the
signature is anchored to the digest of the current text, so the turn that records
*what* was signed and *who* held it has to exist.

```bash
# 1. Open the circuit over the radicado (Salida or Interno: what the institution
#    produces; an incoming document was signed by whoever sent it)
curl -X POST 'http://localhost:19000/api/v1/signature/requests' \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"objeto_tipo": "radicado", "objeto_id": "uuid-radicado",
       "titulo": "Signature of the reply letter",
       "firmantes": ["uuid-signer"]}'
# Response: { "solicitud_id": "uuid", "estado": "pendiente", "items": [ … ] }

# 2. Request the second factor for your own turn (OTP by email, or TOTP)
curl -X POST 'http://localhost:19000/api/v1/signature/requests/uuid-solicitud/challenge' \
  -H "Authorization: Bearer $TOKEN"

# 3. Sign the turn with the code received
curl -X POST 'http://localhost:19000/api/v1/signature/requests/uuid-solicitud/sign' \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"totp_code": "123456"}'
```

Requires `PERM_FIRMA`. If the text changed after the circuit was opened, step 3
answers `409 documento_modificado` instead of signing something else.

### Open a signature chain over an expediente

Several people sign **in order**: turn 2 does not open until turn 1 is resolved
(RF-FIR-04). Each signer sees it in their `/firmas` inbox when their turn comes.

```bash
curl -X POST 'http://localhost:19000/api/v1/signature/requests' \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"objeto_tipo": "expediente", "objeto_id": "uuid-expediente",
       "titulo": "Cierre del expediente",
       "firmantes": ["uuid-first", "uuid-second"]}'
# Response: { "solicitud_id": "uuid", "estado": "pendiente", "items": [ … ] }
```

From the UI, the same act sits behind the **"Cadena de firmas"** button, on the
expediente record (`/expedientes/{id}`, with `USUA_PERM_EXPEDIENTE` +
`PERM_FIRMA`) and in a radicado's drawer in `/bandeja` (with `PERM_RADI` +
`PERM_FIRMA`). Over a radicado it is offered only for **Salida or Interno**: a
signature chain is opened over what the institution *produces*, and an incoming
document was signed by whoever sent it. If the expediente already has an open
chain, the screen shows it and will not let you open another: it must be resolved
or rejected first.

> `objeto_tipo` accepts `expediente`, `radicado` and `borrador`. Drafts still being
> written have their own circuit — visto bueno **and** signature — under
> `/borradores`, because the text can still be edited there.

> **Registering a draft**: on confirmation the screen asks for the **document type** (and, for an outgoing document, the **origin department** among your active ones) plus the type's own metadata; with no active department assigned, "Radicar" is not offered. Stored metadata is then readable in the `/bandeja` detail and in the expediente record.

### Search knowledge (full-text fallback, ADR-046)

> **The knowledge layer is opt-in per entity (ADR-037).** New entities are born with it **off**: the entity administrator (`USUA_PERM_ADMIN` level 3) switches it on in **/admin/parametros** (or with `PUT /api/v1/config/asistente {"habilitado": true}`); until then knowledge routes answer `403 asistente_deshabilitado`. Each person also needs the **`PERM_CONOCIMIENTO`** permission (level 1 queries), granted per group in **/admin/grupos**. Docuboto does not call language models: `/search` and `/antecedentes` reuse the `document` package's full-text search and, when the entity is on `ppi`, are delegated to the provider. There is no generation route.

```bash
curl -X POST 'http://localhost:19000/api/v1/knowledge/search' \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"radicados pendientes","top_k":5}'
# Response: hits from the local full-text index (same shape as document search)
```

### File through the public window (automatic verification, C16)

Unauthenticated intake (`POST /api/v1/public/{tenant}/ventanilla` and the legacy `/pqrs`) requires an **ALTCHA proof of work** on top of the honeypot and the rate limit: the browser solves, on its own and in a few seconds, a challenge the server hands out; the person solves nothing. From a script:

```bash
RETO=$(curl -s http://localhost:19000/api/v1/public/demo/ventanilla/reto)
ALTCHA=$(printf '%s' "$RETO" | python3 scripts/lib/altcha_solver.py)   # a challenge is good ONCE
# `altcha` goes inside the JSON of the multipart `datos` part, next to sitio_web
```

A used, expired or other-entity challenge is rejected (`409 antirrobots_usado`, `422 antirrobots_vencido|antirrobots_invalido`): ask for a new one. `ALTCHA_HMAC_KEY` (≥ 32 bytes, `openssl rand -hex 32`) is required; in development `docker-compose.yml` ships an example. `scripts/verificar-ventanilla.sh` also checks that a challenge is issued.

Whoever identifies themselves with an ID number leaves a **provisional record**; it is not matched to an existing record by number. In **/admin/terceros → "Fichas por conciliar"** (`PERM_RADI` level 3) it is linked to the existing record, confirmed as a new one, or discarded with a reason (see [Domain](domain.md)).

### Declassify a classified radicado (second person) and publish the index

A **classified** radicado (level 3, Law 1712 art. 18) is not lowered with a `PATCH`: it answers `409 desclasificacion_requiere_aprobacion`. One person **requests**, another with `PERM_RECLASIFICAR` level 4 **approves**, and on approval the level drops at once:

```bash
# 1) The requester (PERM_RECLASIFICAR level 3)
curl -X POST "$API/api/v1/documents/$ID/desclasificacion" -H "Authorization: Bearer $TOKEN_A" \
  -H 'Content-Type: application/json' \
  -d '{"nivel_seguridad": 1, "motivo": "The ground for classification has ceased"}'
# 2) The approver (another person, PERM_RECLASIFICAR level 4): sees the queue, opens the radicado and decides
curl "$API/api/v1/desclasificaciones" -H "Authorization: Bearer $TOKEN_B"
curl -X POST "$API/api/v1/desclasificaciones/$REQUEST/aprobar" -H "Authorization: Bearer $TOKEN_B"
```

If the approver is the requester, `409 separacion_de_funciones`; the request expires after 15 days. In the demo, `scripts/seed_demo.py` grants `PERM_RECLASIFICAR` level 4 to the management groups: with no profile at level 4 nobody could approve. When reserving or classifying you must pick a **cause from the closed catalogue** (`GET /api/v1/documents/causales-reserva`); a reserved one (art. 19) also carries a term of 1 to 180 months.

The **public index of classified and reserved information by series** (art. 20 obligation) is read unauthenticated: `curl http://localhost:19000/api/v1/public/archive/indice-clasificados/demo` (or `….csv`). It carries no radicado numbers or counts.

### Lend a physical-archive unit, set a legal retention and record a TVD appraisal (C-5)

1. **Loan**: in `/archivo-fisico`, "Registrar préstamo" asks for the **requester**: *Interno* (search among users who can receive that unit) or *Externo* (name and ID document required). The screen names the unit when the loan is refused (already lent, in a transfer or disposal batch, requester without access).
2. **Legal retention**: on the expediente, the "Retenciones legales" tab (administration): set with reason, grounds and record; lift by repeating the reason. `valoracion_pendiente` is shown but not operable.
3. **Appraisal**: in `/admin/tvd`, with the TVD approved or validated, "Valoración del Comité" records the minutes and releases the expired fonds' expedientes (pending, applied and exhausted are shown there).
4. **Disposal batch**: when proposing, the screen lists the statements that would lose their support; an attachment cited by a current statement cannot be deleted (`anexo_citado_por_constancia`).


## The web UI: first steps

With the platform up, the frontend sits at <http://localhost:19300>.

### 1. The public landing page (`/`)

The site root is a **public page** that needs no session: it describes what OrpycaMCP
is, the regulatory framework it implements (Law 594/2000, AGN Agreement 060/2001), its
capabilities and its lineage from Orfeo, and it closes with the AGPL v3 licence. It is
the product's front door and one of the two places on the site where the full logotype
is used (the other being the sign-in card).

If the browser already holds an active session, `/` **redirects automatically (302) to
`/dashboard`**: an authenticated user never sees the landing page. To see it again, sign
out from the user menu.

From the landing page, "Iniciar sesión" leads to `/login`, the app's own username and
password form: the SSR posts it to `POST /api/v1/auth/login` (ADR-048) and token custody
happens **server-side**; the JWT lives in an `httpOnly` cookie and is never visible to
the browser's JavaScript. The demo entity's people (`operador`, `jefe`, `secretaria`,
`archivista`, `consulta`, `e2e1..3`) are created by `scripts/seed_demo.py` with the
password `orpycamcp_dev`.

### 2. The personal dashboard (`/dashboard`)

After signing in, the dashboard opens with **four personal modules** — what *this* user
has to do, not the tenant's figures:

| Module | What it shows |
|---|---|
| 01 Pending radicados | The workflow steps assigned to the person or their dependency — the **same** query as the `/bandeja` board (`GET /workflows/inbox`), so both count alike |
| 02 Recent activity | The latest radicados that saw movement |
| 03 Alerts | Open radicados — neither closed nor annulled, **including freshly registered ones** — whose due date (`due_date`, RF-RAD-04) is near or past, most overdue first |
| 04 Favourites | The navigation destinations the user has pinned in the sidebar |

The **tenant-wide aggregate metrics** (radicados by status, top dependencias, monthly
activity) are still available, but in a secondary collapsible section, closed by
default, and **only if the user holds the `SGD_PERM_ESTADISTICA` permission** — the same
one that enables the `/reportes` view.

### Not-ready institution

If the institution isn't provisioned yet or its status couldn't be checked, the session shows a dedicated screen («La entidad está suspendida», «La entidad aún no está lista», «No se pudo comprobar el estado de la entidad») with **Reintentar** (when the status may change) and **Cerrar sesión**. On the public counter (`/radicacion`, `/eliminacion`) those same cases appear as «Esta entidad no tiene ventanilla disponible».

### 3. Global search from any screen

The top bar carries a **global search** field available on every authenticated screen.
It can be focused with the <kbd>/</kbd> keyboard shortcut (the shortcut is inhibited if
focus is already inside a text field, so it does not interfere with typing). On narrow
screens the field unfolds from a magnifier icon.

On submit, the search navigates to `/busqueda?q=<term>`, which is the same full-text
search view (PostgreSQL FTS, `GET /api/v1/search` against the app) with its advanced
filters. Because the term travels in the URL, any search is **linkable and shareable**,
and the browser's back button behaves as expected.

### 4. Favourites in the sidebar

Every sidebar entry has a **pin/unpin** control. Pinned items rise to a "Favourites"
section at the top of the menu and also appear in module 04 of the dashboard.
Favourites are stored in the browser, partitioned by institution and user, so two users
on the same team do not share a list.

Favourites **do not bypass RBAC**: if a permission is withdrawn from a user, the
corresponding entry disappears from both the menu and their favourites, with no need to
clean it up by hand. As everywhere in the frontend, this is defence in depth: the
backend revalidates permission and clearance on every request.

See also: [System screens](pantallas.md) for the web-UI counterpart of each of
these examples.
