# Getting Started

## Requirements

- Docker 24+ and Docker Compose v2
- 4 GB RAM minimum (8 GB recommended in production)
- Free ports: 19080, 19180, 15432, 19900, 16379

> Internal services keep listening on 8001-8011/8080 inside the `orpycamcp-net`
> Docker network; what changes is the port **published** to the host (19xxx /
> 15432 / 16379 range) to avoid clashing with other local dev stacks. See the full
> table in [Architecture](architecture.md#services-and-ports).

## Local Installation (development)

```bash
# 1. Clone the repository
git clone https://gitlab.com/orpyca/orfeo_mcp.git
cd orfeo_mcp

# 2. Start the full infrastructure
docker compose --profile dev up -d

# 2.bis (optional) In-house voice dictation: it downloads a transcription model
# and uses CPU, which is why it starts switched off (ADR-020).
docker compose --profile assistant up -d whisper-service

# 3. Verify the services are running
docker compose ps
```

## Access URLs

| Service | URL | Dev credentials |
|---|---|---|
| API Gateway (docs) | http://localhost:19080/docs | — |
| Frontend (SvelteKit) | http://localhost:19300 | — |
| Keycloak Admin | http://localhost:19180/admin | admin / orpycamcp_dev |
| Object store (S3 API) | http://localhost:19900 | orpycamcp / orpycamcp_dev |

> The object store is **RustFS** since ADR-027 (MinIO archived its open-source project and
> pulled its images). It has **no web console** — port 19901 is gone: use `aws-cli` against
> `http://localhost:19900` to inspect buckets.

| PostgreSQL | localhost:15432 | orpycamcp / orpycamcp |
| MailHog (email dev) | http://localhost:8025 | — |

## First Document Registration

A `radicado` is an official document registered with a unique, immutable tracking number.

```bash
# 1. Obtain an access token (via the gateway, not directly against the service)
TOKEN=$(curl -s -X POST 'http://localhost:19080/api/v1/auth/token' \
  -H 'Content-Type: application/json' \
  -d '{"username": "operador", "password": "orpycamcp_dev", "tenant_slug": "demo"}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# 2. Register an incoming document
curl -X POST 'http://localhost:19080/api/v1/documents' \
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

A `tenant` is an institution served by a shared OrpycaMCP installation.

Onboarding an institution is a platform operation: `/api/v1/tenants` is **not exposed by the gateway**
(see [Multi-tenancy](multi-tenancy.md)). Use the provisioning script:

```bash
./scripts/init-tenant.sh mi-entidad "Mi Entidad Pública" MENT
```

This provisions the `tenant_mi_entidad` schema (each tenant's isolated database schema, named `tenant_{slug}`) in PostgreSQL (the slug takes lowercase letters, digits and hyphens; the hyphen becomes an underscore in the schema), applies the migrations and activates the institution.

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
  --output services/archive-service/migrations/003_trd_mi_entidad.sql
```

**3. Apply the migration in the tenant's schema:**

```bash
docker compose exec postgres psql -U orpycamcp -d orpycamcp_db \
  -c "SET search_path TO tenant_mi_entidad;" \
  -f services/archive-service/migrations/003_trd_mi_entidad.sql
```

## More end-to-end usage examples

These examples assume you already have `$TOKEN` from the previous step. The full
contract for each endpoint is in the [API Reference](api.md); the business detail
behind each entity is in [Document domain](domain.md).

### Create an expediente and link a radicado

```bash
# 1. Create the expediente (requires a TRD configured for the series)
curl -X POST 'http://localhost:19080/api/v1/expedientes' \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"nombre": "Contract 2024-001 — Office supplies", "serie_id": "uuid-serie"}'
# Response: { "id": "uuid-expediente", "estado": "open", ... }

# 2. Link the radicado created earlier
curl -X POST 'http://localhost:19080/api/v1/expedientes/uuid-expediente/radicados' \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"radicado_id": "uuid-radicado"}'

# 3. Close the expediente (generates and signs the electronic index, see Lifecycle)
curl -X PATCH 'http://localhost:19080/api/v1/expedientes/uuid-expediente' \
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
curl -X POST 'http://localhost:19080/api/v1/signature/requests' \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"objeto_tipo": "radicado", "objeto_id": "uuid-radicado",
       "titulo": "Signature of the reply letter",
       "firmantes": ["uuid-signer"]}'
# Response: { "solicitud_id": "uuid", "estado": "pendiente", "items": [ … ] }

# 2. Request the second factor for your own turn (OTP by email, or TOTP)
curl -X POST 'http://localhost:19080/api/v1/signature/requests/uuid-solicitud/challenge' \
  -H "Authorization: Bearer $TOKEN"

# 3. Sign the turn with the code received
curl -X POST 'http://localhost:19080/api/v1/signature/requests/uuid-solicitud/sign' \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"totp_code": "123456"}'
```

Requires `PERM_FIRMA`. If the text changed after the circuit was opened, step 3
answers `409 documento_modificado` instead of signing something else.

### Open a signature chain over an expediente

Several people sign **in order**: turn 2 does not open until turn 1 is resolved
(RF-FIR-04). Each signer sees it in their `/firmas` inbox when their turn comes.

```bash
curl -X POST 'http://localhost:19080/api/v1/signature/requests' \
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

### Query the conversational assistant

```bash
curl -X POST 'http://localhost:19080/api/v1/assistant/chat' \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"message": "Which incoming radicados do I have pending this week?"}'
# Response: natural-language text with citations to the radicados consulted (RAG, read-only)
```

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

From the landing page, "Iniciar sesión" leads to `/login`, which starts the OAuth2
Authorization Code + PKCE flow against Keycloak (the code exchange and token custody
happen **server-side**; the JWT lives in an `httpOnly` cookie and is never visible to
the browser's JavaScript).

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

### 3. Global search from any screen

The top bar carries a **global search** field available on every authenticated screen.
It can be focused with the <kbd>/</kbd> keyboard shortcut (the shortcut is inhibited if
focus is already inside a text field, so it does not interfere with typing). On narrow
screens the field unfolds from a magnifier icon.

On submit, the search navigates to `/busqueda?q=<term>`, which is the same full-text
search view (PostgreSQL FTS, `GET /api/v1/search` through the gateway) with its advanced
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
