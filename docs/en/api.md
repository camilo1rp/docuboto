# API Reference

> Base URL in development (from the host, via the gateway's published port): `http://localhost:19080/api/v1`
> All endpoints (except the public ones) require `Authorization: Bearer {token}`.
>
> The "port NNNN" noted in each section below is the **internal** container port
> inside the `orpycamcp-net` Docker network — never accessed directly from the host,
> always through the `api-gateway` (published port 19080). See the full
> internal↔published mapping in [Architecture](architecture.md#services-and-ports).

## Conventions

| Convention     | Value                                                                   |
| -------------- | ----------------------------------------------------------------------- |
| Authentication | `Authorization: Bearer {JWT}`                                           |
| Pagination     | `?page=1&size=20` — response includes `X-Total-Count`                   |
| Dates          | ISO 8601 UTC — `2024-01-15T10:30:00Z`                                   |
| IDs            | UUID v4                                                                 |
| Multi-tenancy  | Header `X-Tenant-Slug` injected by the gateway after validating the JWT |

### Standard error format

```json
{
  "error": "document_not_found",
  "detail": "Radicado 2024-ICETEX-E-000001 no encontrado",
  "status": 404
}
```

---

## auth-service — port 8001

### `POST /api/v1/auth/token` — public

Authentication with credentials. Returns a pair of JWT tokens.

```json
// Request
{ "username": "operador", "password": "orpycamcp_dev", "tenant_slug": "demo" }

// Response 200
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "Bearer",
  "expires_in": 300
}
```

### `POST /api/v1/auth/refresh` — public

Renews the access token with a valid refresh token.

```json
// Request
{ "refresh_token": "eyJ..." }
// Response 200 — same format as /token
```

### `POST /api/v1/auth/logout`

Invalidates the refresh token in Keycloak.

```json
// Request
{ "refresh_token": "eyJ..." }
// Response 204
```

### `GET /api/v1/auth/validate`

Validates a Bearer token. Used internally by the api-gateway.

```json
// Response 200
{
  "valid": true,
  "user_id": "uuid",
  "username": "operador",
  "tenant_slug": "demo",
  "roles": ["operator"]
}
```

### `GET /api/v1/auth/me`

Information about the authenticated user extracted from the token, including their effective permissions resolved from the tenant DB (D-07).

Requires `Authorization: Bearer <token>`. If `X-Tenant-Slug` is supplied, permissions are resolved from the tenant DB. Without that header (superadmin / direct call), `permissions` returns `[]` and `permission_levels` returns `{}` (fail-closed).

```json
// Response 200
{
  "user_id": "uuid",
  "username": "operador",
  "email": "operador@demo.orpycamcp.local",
  "tenant_slug": "demo",
  "roles": ["operator"],
  "permissions": ["PERM_RADI", "USUA_PERM_CONSULTA"],
  "permission_levels": { "PERM_RADI": 3, "USUA_PERM_CONSULTA": 1 }
}
```

- `permissions`: names of the permissions with `crud > 0` assigned to the user through their groups. Users with `is_root=true` always include `USUA_PERM_ROOT`, consistent with `has_permission()` and `GET /users/{id}/permissions`.
- `permission_levels`: **the same keys as `permissions`**, each carrying its **CRUD level** — `0` none · `1` read · `2` edit · `3` create/delete · `4` level_1 · `5` level_2 (`auth_group_permissions.crud`, see `migrations/tenant/001_rbac.sql`). ROOT resolves to `USUA_PERM_ROOT: 5`. This is the field a UI that mirrors the RBAC **must** consult: backend gates use `min_crud=3` (RF-SEG-03), so deciding on the mere presence of a permission offers write actions to users who hold it in read mode — actions the backend denies with `403`. `permissions` is kept for compatibility with existing consumers; the two fields are **additive to each other**, not alternatives.
- If the user is not provisioned in the tenant, `permissions` is `[]` and `permission_levels` is `{}` (fail-closed). Neither field is ever omitted or returned as `null`: a consumer must never be able to read their absence as "no restriction".

### TOTP 2FA — user credential (E06 Inc.6, RF-FIR-13)

The TOTP secret (RFC 6238) is a **durable identity credential** and lives in auth-service (per-tenant table `auth_user_totp`, encrypted with **AESGCM**; AAD `tenant:{slug}:user:{id}:totp`). The plaintext secret is disclosed **exactly once**, in the `otpauth://` URI returned by `enroll` (`Cache-Control: no-store`); it never leaves again. All of these are self-service (the user's own bearer token).

- `POST /api/v1/auth/me/totp/enroll` — generates the secret (20 bytes, base32) in state `pendiente_activacion` and returns `{otpauth_uri, estado}` (for a QR code in an authenticator app). Re-enrolling over an **active** TOTP requires step-up (a currently valid code): `409 totp_ya_activo` / `401 step_up_invalido`.
- `POST /api/v1/auth/me/totp/activate` `{code}` — activates the credential with a first valid code → `{estado:"activo", activated_at}`. `409 totp_no_pendiente`; `401 codigo_invalido` (increments `failed_attempts`); `423 totp_bloqueado`.
- `GET /api/v1/auth/me/totp` — `{estado, activated_at, locked_until}`. **Never** returns the secret.
- `DELETE /api/v1/auth/me/totp` `{step_up_totp_code | step_up_recovery_code}` — revokes the **active** credential, requiring step-up → `{estado:"revocado"}`. `401 step_up_invalido`; `404 no_totp`.
- `POST /api/v1/auth/me/totp/recovery-codes` `{step_up_totp_code | step_up_recovery_code}` — **regenerates** the batch of recovery codes, invalidating the previous one entirely. `200 {recovery_codes:[…]}` with `Cache-Control: no-store`. `409 totp_no_activo`; `401 step_up_invalido`; `423 totp_bloqueado`.
- `POST /api/v1/auth/users/{user_id}/totp/revoke` `{motivo}` — **administrative reset** (`USUA_PERM_ADMIN`): removes **another** person's second factor so they can register it again themselves from `/perfil`, and voids their recovery codes in the same transaction. `200 {estado:"revocado", usuario_id, estado_previo, codigos_recuperacion_eliminados}`; `404 no_totp`; `409 restablecimiento_propio_no_permitido`; `422` if `motivo` is shorter than 8 characters. It **never** returns a secret nor enrolls on anyone's behalf — whoever could enroll for someone else would in effect hold their personal signature. The `totp.restablecido_por_admin` entry is attributed to the **administrator** (`actor`), with the affected user in `object_ref` and the `motivo` in the `payload`.
  - **Why it exists, and why it was not solved by accepting recovery codes when signing**: recovery codes restore the factor but **cannot sign** (`/internal/totp/verify` only classifies a TOTP), and `enroll`/`DELETE /me/totp` require the current code or a recovery code. Anyone who lost the phone **and** the codes was locked out, with no way out other than touching the database by hand. Letting a long-lived paper code sign would have downgraded the signing second factor in the very act that attests: the factor is restored, not degraded.
  - **It cannot be used on oneself** (`409`): an administrator who reset their own could remove it without proving device possession and enroll another right after — whoever stole their session would end up holding the second factor of their personal signature. **Two people** are always required; the administrator uses `/perfil` like everyone else.

**Recovery codes (E06, migration 015).** TOTP is a **possession** factor, and until this migration **every** exit from it — revoking it, re-enrolling it, and revoking the personal signing key — required a current TOTP code, i.e. required the very phone that had been lost: whoever lost it was **locked out**, with no way out other than manual intervention on the database. Now `activate` returns **10 single-use codes** (`recovery_codes`, `Cache-Control: no-store`) and any of them works as `step_up_recovery_code` in `enroll` (re-enrolling) and in `DELETE /me/totp` (revoking).

- They are stored **hashed** (scrypt with a per-code salt), not encrypted: unlike the TOTP secret, the server never needs them in plaintext, only to check them — so a dump of the table does not allow authenticating. They are shown **exactly once**; `GET /me/totp` returns `recovery_codes_restantes` (a count), never the codes.
- **Single use**, guaranteed by an `UPDATE ... WHERE used_at IS NULL`: two concurrent requests with the same code race for the row and only one wins.
- They are issued **on activation**, not on enrollment (until then the credential protects nothing) and in the **same transaction** as the activation — there is no instant with an active TOTP and no recovery path. Revoking or re-enrolling **deletes** the batch in the same transaction.
- **Deliberate boundary: they do not work for SIGNING.** `POST /internal/totp/verify`, which signature-service invokes when signing, does **not** accept them (its `code` is validated as a 6-digit TOTP). A recovery code is written on paper or held in a password manager: it is not possession of a device, and admitting it as the second factor of a signing act would weaken the attribution RF-FIR-13 exists for. Recovering access and signing are different acts and demand different proof.
- _(Internal, service-to-service inside the Docker network, **not** routed by the gateway)_ `POST /internal/totp/verify` `{code, contexto?}` — called by signature-service when signing; it derives the identity from the **forwarded bearer token** (not from a body `user_id` → no IDOR), honours `locked_until` before comparing, and advances the `last_timestep` anti-replay counter in a single atomic `UPDATE`. Lockout after `TOTP_MAX_FAILED_ATTEMPTS` (default 5) for `TOTP_LOCKOUT_SECONDS` (default 900). Skew window ±`TOTP_SKEW_STEPS` (default 1).

**PERSONAL PKI signing key (E06/E17 F4, Inc.1 — server custody, `acreditado=false`).** Each user's private signing key lives **only in auth-service**, AESGCM-encrypted (KEK `SIGNING_KEY_ENCRYPTION_KEY`, **separate** from the TOTP one), and it NEVER leaves the service.

- `POST /api/v1/auth/me/signing-key/enroll` — generates the RSA-2048 pair in memory, encrypts the private key, issues a **CSR** (subject = the user's identity + `cargo`/job title on a best-effort basis from RBAC groups) and stays `pendiente_emision`; returns **only** `{csr_pem, estado}` — **never** the private key. Requires `PERM_FIRMA`. A re-enroll revokes the previously active key (step-up hardening deferred to Inc.2). Certificate issuance is **out-of-band** (`scripts/gen-user-cert.sh`, run with `ca.key` outside any container; the user sub-CA is distinct from the institutional seal's).
- `GET /api/v1/auth/me/signing-key` — `{estado, certificado_fingerprint?, not_after?}`. **Never** returns the private key.
- `DELETE /api/v1/auth/me/signing-key` `{step_up_totp_code}` — **revokes one's own key**, requiring TOTP step-up (verify-and-consume; without a code → `422`) → `{estado:"revocado"}` (E06/E17 Inc.2). Writes a `signing_key.revocada` audit entry (with `revocado_at`/`motivo`) **atomically** with the UPDATE. `404` if there is no key.
- `POST /api/v1/auth/admin/users/{user_id}/signing-key/revoke` `{motivo?}` — an **admin** (`USUA_PERM_ADMIN`) revokes another user's key (the "departed employee" case): the target `user_id` travels in the path, the actor comes from the token (the two can never be confused). Atomic audit entry with `revocado_por`.
- _(Internal, **not** routed by the gateway)_ `GET /internal/signing-key/revocation-status?fingerprint=<hex>` — queried by signature-service when verifying a personal signature (`X-Internal-Token` + `X-Tenant-Slug`): it returns **only** `{status, revocado_at?}` (`status ∈ vigente|revocado|desconocido`) — never the certificate or the key. Tenant-isolated: a fingerprint from another tenant, or a non-existent one, returns `desconocido` (fail-closed, never `vigente`). The pending→active activation persists `user_ca_fingerprint`+`certificado_fingerprint` and validates `leaf.verify_directly_issued_by(sub_ca)` fail-closed.
- _(Internal, **not** routed by the gateway)_ `POST /internal/signing-key/sign-digest` `{digest_b64, alg:"RSA-SHA256", prehashed:true, totp_code}` — called by signature-service when signing a personal act in the chain: it derives the user from the **forwarded bearer token** (no IDOR), **verifies-and-consumes the TOTP atomically** with the signature, decrypts the key **in memory only** (minimum lifetime), signs the digest, and returns `{signature_value_b64, certificado_pem, certificado_fingerprint, serial, not_after}` — the private key is never returned. This is the "sign a digest" interface of an HSM (the seam towards qualified signature). `X-Internal-Token` is compared with `compare_digest`. The pending→active activation leaves an atomic `signing_key.activada` audit entry.

### RBAC user management (E14)

- `POST /api/v1/auth/users` · `GET /api/v1/auth/users` (paginated, `X-Total-Count`) · `GET /api/v1/auth/users/{id}/permissions` — user administration. The whole router requires `USUA_PERM_ADMIN`. `POST` writes an `rbac.usuario_creado` entry to `audit_log`, **atomically** with the INSERT (see the audit note below).
  - **`POST` with `is_root: true` → `403 forbidden` if the caller is not ROOT** ([ADR-024](adr/ADR-024-root-fuera-de-banda.md), closing a High self-escalation finding). A non-ROOT `USUA_PERM_ADMIN` can never create a ROOT user; ROOT can. A tenant's first ROOT is provisioned out-of-band (`app/ops/grant_root.py`, not over HTTP).
- `GET /api/v1/auth/users/pickable?size=N` — minimal projection `[{id, username}]` of **active** users, for UI pickers (e.g. the `UserPicker` used for approval chains, T-08c). Gated with `PERM_RADI` (not admin); it exposes neither `email` nor administrative metadata (D-10). `size` in `[1, 500]`.

### URD and dependencia context (E08)

- `GET /api/v1/auth/users/{user_id}/urd` · `POST` · `DELETE /{urd_id}` — manages the User-Role-Dependencia assignments (one per (user, dependencia); a single primary one). Requires `USUA_PERM_ADMIN`. `POST`/`DELETE` write `urd.creada`/`urd.eliminada` entries, **atomically** with the mutation.
- `GET /api/v1/auth/context` — caller context: `{user_id, active_depe_id, available_depts[]}`.
- `POST /api/v1/auth/context/switch` `{ "depe_id": 120 }` — sets the active dependencia (organizational unit) for the user themselves. **Does not reissue the token** (ADR-013): it persists the context and audits it in `usua_historico` (Channel B, not `audit_log`). `400 no_urd_for_dependency` if the user has no active URD there.

### Groups, memberships and permissions (E08)

The whole router requires `USUA_PERM_ADMIN`.

- `POST /api/v1/auth/groups` · `GET /api/v1/auth/groups` — create and list groups. `POST` writes an `rbac.grupo_creado` entry.
- `GET /api/v1/auth/permissions` — permission catalog (`{id, nombre, descripcion}`). **`USUA_PERM_ROOT` is omitted from the catalog if the caller is not ROOT** ([ADR-024](adr/ADR-024-root-fuera-de-banda.md)) — an option the `PUT .../permissions/{id}` below would reject is not offered in the first place.
- `POST /api/v1/auth/groups/{group_id}/members/{user_id}` · `DELETE .../members/{user_id}` — add/remove a user from a group. They write `rbac.miembro_agregado`/`rbac.miembro_removido` entries.
- `GET /api/v1/auth/groups/{group_id}/members` (paginated, `X-Total-Count`) — the group's users. The read mirror of the add/remove above.
- `PUT /api/v1/auth/groups/{group_id}/permissions/{permission_id}` `{ "crud": 3 }` — sets the CRUD level (0-5) of a permission for the group. Writes an `rbac.permiso_grupo_asignado` entry (payload with `permission_id`/`crud`).
  - **Granting (`crud > 0`) `USUA_PERM_ROOT` → `403 forbidden` if the caller is not ROOT** ([ADR-024](adr/ADR-024-root-fuera-de-banda.md)). Revoking (`crud = 0`) is always allowed, even for a non-ROOT — only granting is blocked. This closes the self-escalation path through a group the actor is themselves a member of.
- `GET /api/v1/auth/groups/{group_id}/permissions` — permissions assigned to the group: `[{permission_id, nombre, descripcion, crud}]`. The read mirror of the `PUT` above.
- A `group_id` that does not exist **or belongs to another tenant** → an indistinguishable `404` (the `search_path` already scopes the tenant schema; never `403`, which would confirm the other tenant's group exists).

**Auditing of RBAC/URD writes (E08, closing a traceability gap):** every `POST`/`PUT`/`DELETE` in this section and in "URD" writes an entry to `public.audit_log` **within the same transaction** as the mutation (`async with conn.transaction()`) — not _best-effort_: if `audit.append` fails, the mutation is rolled back; if the audit library is not installed, the write responds `500 auditoria_no_disponible` **before** touching any row (the same fail-closed pattern as `signing-key`/`totp`). `tenant_slug` is internally mandatory (`X-Tenant-Slug`, already required by `get_tenant_conn`); the actor is the `X-User-Id` resolved by the gate.

### Security classification (E08, RF-SEG-08, Law 1712/2014)

- `GET /api/v1/auth/security-levels` — catalog of levels (1 Public, 2 Reserved, 3 Classified).
- `PUT /api/v1/auth/groups/{group_id}/clearance` `{ "max_level": 2 }` — sets the maximum accessible level for a group (requires `USUA_PERM_ADMIN`). Writes a `clearance.grupo_actualizado` entry with `{group_id, previous_max_level, new_max_level}` (**atomic**, same fail-closed guarantees as RBAC/URD above) — the previous level is what lets an upgrade be told apart from a downgrade.
  - **No write-up → `403 forbidden` if `max_level` exceeds the caller's effective clearance** ([ADR-024](adr/ADR-024-root-fuera-de-banda.md)). An admin with Reserved (2) clearance cannot set Classified (3) on any group, including their own; ROOT is not subject to this limit (full bypass, `_MAX_LEVEL`=3).
- `GET /api/v1/auth/groups/{group_id}/clearance` — the group's current clearance: `{group_id, max_level}` (`max_level: null` if the group has no row in `role_clearance`, which implies PUBLIC=1 by default). Requires `USUA_PERM_ADMIN`; same indistinguishable `404` as the `PUT` for a non-existent group or one from another tenant.
- `GET /api/v1/auth/clearance` — caller clearance: `{user_id, max_level, accessible_levels[]}` (MAX across their groups; ROOT = 3).
- `GET /api/v1/auth/clearance/check?level=N` — `{level, max_level, allowed}` (least privilege: `allowed = max_level ≥ level`).

### `GET /api/v1/audit`

Queries the tenant's immutable audit log (ADR-008), most recent first. Requires `USUA_PERM_ADMIN`. Filters: `?action=&object_type=&object_ref=&actor=&page=1&size=20`. Header `X-Total-Count`.

**Clearance-trimmed (no read-up, RF-SEG-08).** `USUA_PERM_ADMIN` is administrative competence, not a security clearance: administering the tenant does not authorise knowing the classified matters processed within it. Entries whose object is a document or file above the caller's effective clearance are **neither listed nor counted** (`X-Total-Count` reflects the same trimming; otherwise the total would be an oracle for how many classified documents exist). Details:

- **Allow-list, not deny-list**: what gets listed is the types that _cannot_ refer to a document or file (identity, RBAC, org structure, instruments and catalogues: `usuario`, `grupo`, `membresia`, `dependencia`, `trd_serie`, `parametro`, `auth_session`…) plus classifiable ones resolving to a reachable level. **Everything else is left out.** Why: `audit_log` is written by eleven services, and several types that do not denote a document carry its identity in the `payload` — `evento` (dead-letter) serialises the whole envelope including `business.tracking_number`, `import` stores the literal list of imported tracking numbers, `file` stores the attachment filename. With a deny-list, every new type or service is born visible and the omission is silent.
- The level is resolved **live** against the tenant schema's `radicados` / `expedientes` — never denormalised, so a reclassification takes effect immediately. The schema is anchored to the request's `tenant_slug`, not to the connection's `search_path`.
- **Fail-closed**: an `object_ref` that resolves to no row (deleted object, different reference format) is left out; what cannot be classified is not declared public. Likewise if the tenant lacks the source tables.
- Filtering by the `object_ref` of an out-of-reach document returns zero entries, indistinguishable from it not existing.
- At maximum clearance (3 = Classified, and ROOT) **no trimming is applied**: the audit log is served in full, which is what that clearance authorises.
- **Declared residue**: `id` is a monotonic sequence, so gaps between visible ids reveal _how many_ entries were hidden and, via neighbouring `ts`, _when_. Not closed because `id` is what matches `broken_id` from `/audit/verify`; documented instead.

```json
[
  {
    "id": 42,
    "ts": "2024-01-15T10:30:00Z",
    "service": "document-service",
    "actor": "uuid",
    "canal": "api",
    "action": "document.radicado_created",
    "object_type": "radicado",
    "object_ref": "2024-ICETEX-E-000001",
    "payload": { "...": "..." },
    "hash": "sha256..."
  }
]
```

### `GET /api/v1/audit/verify`

Recomputes the tenant's hash chain and reports its integrity (ADR-008). Requires `USUA_PERM_ADMIN`. **Not** clearance-trimmed: the chain only has evidentiary value if recomputed whole, and over a subset it would always read as "broken". The response is content-free (a boolean and, at most, the id of the row where it breaks), so it reveals nothing about the object.

```json
{ "tenant_slug": "icetex", "ok": true, "broken_id": null }
```

- `ok=false` with `broken_id` = id of the first row whose chain does not match.

---

## tenant-service — port 8002

### `POST /api/v1/tenants` — **NOT routed by the gateway**

> **Closing a Critical finding (2026-07 audit):** the tenant registry lives in the
> `public` schema (no tenant `search_path`) and had **no** authorization gate at
> all — any authenticated user of institution A could enumerate (`GET`) and
> **write** (`PATCH`) institution B's record. The surgical fix was to remove the
> `/api/v1/tenants/` prefix from the gateway's routing table
> (`api-gateway/app/routers/proxy.py`): there is no legitimate external consumer
> (the frontend never calls it, and tenant creation is done by
> `scripts/init_tenant.py` writing straight to `public.tenants` — a platform
> operation, not an application one). The endpoints still exist in tenant-service
> for that script and internal use, but they are **no longer reachable** from
> `http://localhost:19080` — a request to `/api/v1/tenants/` answers
> `404 route_not_found` without even getting as far as validating the token.

Creates an institution and provisions its PostgreSQL schema `tenant_{slug}`.

```json
// Request
{ "slug": "icetex", "name": "Instituto Colombiano de Crédito Educativo", "code": "ICETEX" }

// Response 201
{
  "id": "uuid",
  "slug": "icetex",
  "name": "Instituto Colombiano de Crédito Educativo",
  "code": "ICETEX",
  "status": "active",
  "created_at": "2024-01-15T10:30:00Z",
  "updated_at": "2024-01-15T10:30:00Z"
}
```

- `slug`: lowercase letters, numbers and hyphens only (`^[a-z0-9-]+$`) — immutable
- `code`: alphanumeric uppercase (`^[A-Z0-9_]+$`) — appears in radicado (official tracking record) numbers — immutable
- 409 if slug or code already exist

### `GET /api/v1/tenants` — NOT routed by the gateway

Lists institutions. Query: `?status=active&page=1&size=20`. Header `X-Total-Count`.

### `GET /api/v1/tenants/{id}` — NOT routed by the gateway

Get by UUID.

### `GET /api/v1/tenants/by-slug/{slug}` — NOT routed by the gateway

Get by slug. Used by other services to validate the JWT's tenant.

### `PATCH /api/v1/tenants/{id}` — NOT routed by the gateway

Update `name` or `status`. `slug` and `code` are immutable.

### Tenant administration (E14)

> **Authorization (closing a Critical finding, 2026-07 audit):** the service had
> **no** authorization gate whatsoever (`app/core/authz.py` did not exist) — any
> authenticated user of the tenant could create/edit/delete dependencias,
> catalogues, holidays and parameters. Writes now require `USUA_PERM_ADMIN`
> (`app/core/authz.py`, the same `require_permission` pattern as
> document-service/archive-service) and write an entry to `public.audit_log`
> within the same transaction (fail-closed, `RBACService` pattern). The `GET`s
> remain **authenticated only**: they are catalogues and calendar data that the
> registration UI and the deadline computation (RF-RAD-04) need for every user,
> not just administrators.

- **Dependencias** (organizational chart): `GET /api/v1/dependencias` (authenticated) · `POST/PATCH/DELETE /api/v1/dependencias` (**`USUA_PERM_ADMIN`**) · `GET /api/v1/dependencias/by-codigo/{codigo}` (hierarchical, authenticated).
- **Catálogos** (lookups): `GET /api/v1/catalogos/{catalogo}` (authenticated) · `POST/PATCH/DELETE /api/v1/catalogos/{catalogo}` (**`USUA_PERM_ADMIN`**) — `tipos-identificacion`, `tipos-remitente`, `medios-recepcion`, `tipos-anexo`, `causales`, `formas-envio`, `soportes`, `mensajes-rapidos`.
  - **`causales` declares the act it serves** (`ambito`, tenant migration 008): `devolucion`, `anulacion`, `reasignacion`, `devolucion_postal` or `general`. `GET /api/v1/catalogos/causales?ambito=devolucion` filters at the source, and `ambito` travels in the `POST`/`PATCH` body. It is the only catalogue that supports it: asking for it on any other returns **422 `ambito_no_aplica`** instead of ignoring it silently. A causal with no declared ambit stays `general` and every act offers it.
- **Parameters**: `GET /api/v1/config/params`, `GET /api/v1/config/params/{key}` (authenticated) · `PUT /api/v1/config/params/{key}` (**`USUA_PERM_ADMIN`**).
- **Non-business days / holidays**: `GET /api/v1/config/holidays` (`?year=`, authenticated) · `POST /api/v1/config/holidays`, `DELETE /api/v1/config/holidays/{id}`, `POST /api/v1/config/holidays/seed` (`{year_from, year_to}` → seeds the automatically computed Colombian holidays) — all three writes require **`USUA_PERM_ADMIN`**.
- **Business days**: `GET /api/v1/business-days/calculate?from=&days=` (authenticated) — computes the due date applying weekends + holidays + the tenant's non-working days (basis for RF-RAD-04).

### PINAR — Institutional Archives Plan (E14, RF-ADM-08, AGN Agreement 003/2015)

A per-tenant archival planning instrument. Every write (plan lifecycle, critical
aspects, prioritization, objectives, projects, follow-up, instruments) requires
**`USUA_PERM_ADMIN`** (`app/core/authz.py`, closing the same Critical finding as
catalogues/dependencias/config — there was previously no role gate at all, even
though spec §10.3 restricts management to the administrator). **Since
2026-08-28 so do the `GET`s** (§4.bis of the test plan): the gate lives on the
`APIRouter` itself, so every endpoint added here inherits it. Before, the 11
reads were "authenticated only" while the sidebar and the `/admin` cards hid
the screen — a system that hides something and serves it at the same time has
decided nothing. It closes rather than opens because a plan in `borrador` is the
Committee's working material, and because publishing the approved PINAR (Decreto
1080/2015 art. 2.8.2.5.8) is an act directed outwards — a public screen — and not
an ungated internal `GET` that merely resembles one. The accountable actor
arrives in `X-User-Id`.

- **Axes** (fixed catalog, seed of 5): `GET /api/v1/pinar/ejes`.
- **Plan**: `POST /api/v1/pinar/planes` (creates in `borrador`, `version = max+1`) · `GET /api/v1/pinar/planes` (`?estado=`) · `GET /api/v1/pinar/planes/{id}` · `PATCH /api/v1/pinar/planes/{id}` (vision/validity period, only while in `borrador`).
- **Transitions**: `POST /planes/{id}/aprobar` (`borrador→aprobado`, requires `acto_administrativo` → 422 if missing) · `POST /planes/{id}/ejecutar` (`aprobado→en_ejecucion`, **a single plan in execution per tenant** → 409 `plan_en_ejecucion_existe`) · `POST /planes/{id}/cerrar` (terminal). Every lifecycle act is **accountable** (X-User-Id mandatory → 400 if missing) and lands in the **IMMUTABLE `audit_log` (E08)**. On **approval** the content is **frozen** (no further structural editing, follow-up only).
- **Critical aspects** (diagnosis): `POST/GET /planes/{id}/aspectos`, `PATCH/DELETE /aspectos/{id}`.
- **Prioritization** (aspect×axis matrix, impact 1–10): `PUT /planes/{id}/priorizacion` (loads the matrix) · `GET /planes/{id}/priorizacion` → each aspect with its priority = **Σ impacts**, sorted descending (this defines the roadmap).
- **Objectives**: `POST/GET /planes/{id}/objetivos`, `PATCH/DELETE /objetivos/{id}`.
- **Projects**: `POST/GET /planes/{id}/proyectos`, `PATCH/DELETE /proyectos/{id}`, `PUT /proyectos/{id}/aspectos` (link to the aspects it mitigates).
- **Follow-up** (append-only): `POST /proyectos/{id}/seguimiento` (projects the project's `avance_pct`) · `GET /proyectos/{id}/seguimiento`.
- **Dashboards**: `GET /planes/{id}/tablero` (progress by objective and by axis) · `GET /planes/{id}/mapa-ruta` (schedule by date).
- **Articulation** (by reference, not copy): `POST/GET /planes/{id}/instrumentos`, `DELETE /planes/{id}/instrumentos/{id}` — `tipo ∈ {ccd, trd, fuid, plan_preservacion, plan_contingencia}`.

Structural editing is allowed **only in `borrador`** (→ 409 `plan_no_editable` in `aprobado`/`en_ejecucion`/`cerrado`: content freezes on approval); follow-up is allowed in `{aprobado, en_ejecucion}` (→ 409 `plan_no_seguible`).

---

## document-service — port 8003

> Requires the `X-Tenant-Slug` and `X-User-Id` headers (injected by the gateway).

### `POST /api/v1/documents`

Registers a new radicado (official tracking record). Assigns an atomic radicado number.

```json
// Request
{
  "doc_type": "E",
  "subject": "Solicitud de certificado de notas",
  "doc_class": "Derechos de Petición",
  "dest_dept_code": 120,
  "sender_name": "María García",
  "sender_entity": "Ciudadana",
  "pages": 2,
  "response_days": 15,
  "metadata": { "area": "juridica", "prioridad": "alta" },
  "anexos": [
    { "file_id": "uuid", "filename": "solicitud.pdf", "file_size": 45231, "mime_type": "application/pdf",
      "tipo_anexo": "soporte", "es_principal": true, "folios": 3 }
  ]
}

// Response 201
{
  "id": "uuid",
  "tracking_number": "2024-ICETEX-E-000001",
  "doc_type": "E",
  "year": 2024,
  "sequence": 1,
  "subject": "Solicitud de certificado de notas",
  "status": "registered",
  "registered_at": "2024-01-15T10:30:00Z",
  "due_date": "2024-02-05",
  "dest_dept": "Registro y Control",
  "dest_dept_code": 120,
  "anexos": [{ "id": "uuid", "file_id": "uuid", "filename": "solicitud.pdf", ... }]
}
```

- `doc_type`: `E` (Entrada / inbound) | `S` (Salida / outbound) | `I` (Interno / internal)
- `tracking_number`: immutable once assigned
- `response_days` (optional): response deadline in **business days** (RF-RAD-04). If provided, document-service computes `due_date` by querying `tenant-service` (`GET /api/v1/business-days/calculate`, which applies Colombian holidays and the tenant's non-working days). If omitted, `due_date` is `null`.
- `due_date`: due date (ISO `YYYY-MM-DD`) or `null`.
- `origin_dept_code` / `dest_dept_code` (optional): code (`depe_codi`) of the dependencia (organizational unit) in the organizational chart. If provided, document-service validates them against `tenant-service` (`GET /api/v1/dependencias/by-codigo/{codigo}`) and **denormalizes the name** of the dependencia into `origin_dept`/`dest_dept` (legal snapshot). Nonexistent code or inactive dependencia → `400 invalid_dependencia`.
- If `tenant-service` is unavailable when resolving the deadline or the dependencia, creation responds `502 tenant_service_unavailable` and the document is **not** registered.
- `metadata` (optional): variable metadata of the document type (ADR-007). If the `doc_class` has an **active template**, the values are validated against its JSON Schema before registration; if they do not comply → `422 metadata_validation_failed` and the document is not registered. Without a template, they are stored as-is. The column is indexed with GIN.

> **Access control (metadata catalog):** the **mutations** — `POST /api/v1/metadata/templates` and `POST/PATCH/DELETE /api/v1/metadata/elements` — require **`USUA_PERM_TRD`** (fail-closed: `403` without tenant/user-id/permission), the same permission that governs its twin `POST /api/v1/expediente-metadata/templates` in archive-service (a template that relaxes the mandatory validation of a document type is every bit as consequential as an expediente's). `DELETE /metadata/elements/{id}` additionally requires **`crud≥3`** (RF-SEG-03; precedent: `DELETE /tipos-documentales/{id}`). All four writes leave an `audit_log` entry. The `GET` **reads** remain open (tenant only).

### `POST /api/v1/metadata/templates`

Defines the metadata template (JSON Schema) of a document type. A single active template per `tipo_documental`.

```json
// Request
{
  "tipo_documental": "Factura",
  "version": 1,
  "activo": true,
  "json_schema": {
    "type": "object",
    "required": ["valor"],
    "properties": { "valor": { "type": "number" } }
  }
}
```

- `422 invalid_json_schema` if `json_schema` is not a valid JSON Schema; `409 conflict` if that version, or an active one for the type, already exists.

### `GET /api/v1/metadata/templates`

Lists templates; optional filter `?tipo_documental=Factura`.

### `/api/v1/metadata/elements` (metadata element catalog, RF-MET-03)

CRUD for reusable metadata field definitions (`POST`/`GET`/`GET {id}`/`PATCH {id}`/`DELETE {id}`). Attributes: `clave`, `etiqueta`, `tipo_dato` (text/number/date/boolean/select), `longitud`, `ocurrencia_min/max`, `modificable`, `valor_default`, `opciones`, `orden`, `searchable`, `mapeo_dublin_core` (RF-MET-09 hook). `409` if the key already exists. `POST`/`PATCH` require `USUA_PERM_TRD`; `DELETE` requires `USUA_PERM_TRD` with `crud≥3`.

### `GET /api/v1/documents`

List radicados. Filters: `?doc_type=E&status=registered&page=1&size=20`. **Filter by metadata** (RF-MET-02): `?meta.<campo>=valor` (one or several) uses JSONB containment over the GIN index, e.g. `?meta.area=juridica`. **Access control (RF-SEG-08):** the listing is scoped by the radicado's classification exactly as search is — each user only sees those with `nivel_seguridad ≤` their clearance (_fail-closed_ to PUBLIC).

### `GET` / `PATCH /api/v1/documents/{id}/disposition`

Document disposition metadata (RF-MET-08): `programa`, `retention_until`, `accion` (conserve/delete/transfer/select), `confirmado`, `marcado_eliminacion`, `fundamento`. `PATCH` merges the provided fields. **`GET` requires `USUA_PERM_EXPEDIENTE`; `PATCH` additionally requires `crud≥3`** — read level used to be enough to mark an official document for destruction, the same permission and the same level as consulting it (this is the threshold `storage-service` already used for its own dispositions).

`fundamento` is **mandatory** when `accion="eliminar"` or `marcado_eliminacion=true` (`422` otherwise): destruction is irreversible (Law 594/2000 art. 24) and must be justified. It is not required on the non-destructive path, so the field is not devalued by filling it out of routine. Every `PATCH` writes a `document.disposition_set` entry to `audit_log` **including the previous state**, in the same transaction as the write.

> Declared scope: disposition is **not yet derived from the TRD**. The retention schedule lives in `archive-service` and the radicado does not store its series, so deriving it requires a model change and a cross-service contract; until then, the justification and the audit entry are the compensating control.

**Access control (RF-SEG-08):** `404` both when the radicado does not exist and when it exceeds the caller's clearance — indistinguishable, so as not to reveal the existence of classified material.

### `POST /api/v1/documents/{id}/publicacion`

**Proactive publication** act (Law 1712/2014 art. 12). Body: `{publicar: bool, fundamento: str}` — the justification is mandatory in **both** directions (`422` otherwise), because withdrawing a publication is also a decision that must be explainable. Response: `{tracking_number, publicable, publicado_at}`.

Requires `PERM_RECLASIFICAR` with `crud≥3`: the same permission that decides **reservation**, because publishing and classifying are two faces of the same authority over the document's access regime; splitting them across separate permissions would let someone who cannot declassify publish anyway.

Before this endpoint, "public" was **inferred**: a radicado with `nivel_seguridad=1` and not annulled was harvestable over OAI-PMH and served over CMIS — that is, the absence of a reservation decision was read as a decision to publish. `publicable` now starts at `FALSE` and only this act changes it, leaving an audit entry (`document.publicacion_activada` / `document.publicacion_retirada`).

The public cut requires **all three** conditions at once (`publicable AND nivel_seguridad = 1 AND status <> 'anulado'`, single chokepoint in `oai_repository._PUBLIC`), and the `UPDATE` additionally requires `nivel_seguridad = 1` in its own `WHERE`: marking a reserved document as publishable is not a case to filter out later, it is an operation that must not exist — if stored, the next declassification would publish it without anyone having decided so. Neutral `404` for "does not exist", "exceeds your clearance", "not public level" and "annulled".

> **Compatibility**: existing radicados become **non-publishable** after migration `019_radicado_publicable.sql`. An institution using the harvesting channels must explicitly publish whatever it wants to disseminate.

**Reading the state.** `publicable` and `publicado_at` are carried in `DocumentResponse` — that is, in `GET /documents/{id}` and in the listing. Adding them was not cosmetic: the act could be **executed but not consulted**, so neither could the UI offer it over a known state nor could the entity attest to what it had published. A publication scheme that cannot be read is not a scheme, it is a blind write.

**Interaction with reservation (fixed after audit, 2026-08-13)**: classifying a radicado (`PATCH /{id}/security-level` to level ≥ 2) **withdraws its publication** in the same transaction — the flag used to stay latent, and the next declassification, made for an unrelated purpose, returned it to the channels with no act. And the `UPDATE` requires `nivel_seguridad = 1` **only when publishing**: withdrawing is never blocked, not even on an already-reserved radicado, which is where it matters most. The CMIS content cut (`getContentStream`) now requires `publicable` just like the metadata one: it carried a hand-written copy of the `WHERE` that had not been updated.

**Screen**: the act is performed from the "Publicación" drawer in `/bandeja`, which always offers the act opposite to the current state and offers no form at all over a reserved or annulled radicado — the backend's neutral `404` is correct, but letting someone draft a justification only to receive "does not exist" afterwards would be a screen that deceives.

### `GET /api/v1/assistant/alcance`

The caller's scope frame: **what they can do, what they can consult, and what they cannot — with their alternative route**. No permission of its own: it describes the asker's capabilities, resolved from their token.

It exists because permissions had been a silent filter: the catalogue hid what the user could not use, so the limit was discovered **by failing** — or by waiting for an action that was never going to happen. The frame states it up front and offers the way out when there is one ("you cannot send it, but you can transfer the radicado to correspondence").

It is **derived**, not maintained: from the tool catalogue — which declares permission, level, domain operation and alternative route — crossed with `/auth/me`, `/auth/clearance` and `/auth/context`. Adding a tool updates the frame by itself.

It includes `limites_declarados`: what the system does **not** distinguish, so as not to promise precision it lacks — the registration permission does not separate by document type (`PERM_RADI_ENTRADA`/`PERM_RADI_INTERNO` exist and **no gate uses them**), and reads are bounded by security level, not by dependencia.

It lives under `/api/v1/assistant/` because that is the only mcp-server prefix the gateway routes. It does not require the assistant to be available: knowing what you can do should not depend on a model being up.

### `POST /api/v1/expedientes/{id}/publicacion`

**Proactive publication** act for the expediente (Ley 1712/2014 art. 12, archive migration `042`). Same contract as the radicado twin: `{publicar: bool, fundamento: str}` → `{code, publicable, publicado_at}`, `PERM_RECLASIFICAR` at `crud≥3`, justification mandatory in both directions, act and audit entry in a single transaction.

It closes the asymmetry the cross audit uncovered: the art. 12 correction had been applied to radicados and **not here**, so archive-service's `oai_repository._PUBLIC` was still `nivel_seguridad = 1` and the multi-level EAD OAI kept proactively disseminating every unclassified expediente — the column's default. The asymmetry was also implicit: anyone reading document-service's cut would assume this service did the same.

Two **deliberate** differences from radicados:

- The cut carries **no status condition**. An annulled radicado is withdrawn from circulation; a `closed`/`transferred` expediente is not — those are normal lifecycle states. Copying `status <> …` for symmetry would have silently unpublished the entire closed archive, which is most of a fonds.
- What is disseminated is aggregate: code, name, description, extreme dates and the **count** of radicados. Child `tracking_number`s are never enumerated (a public expediente may contain reserved radicados).

`publicable` and `publicado_at` are carried in `ExpedienteResponse`. **Compatibility**: existing expedientes become **non-publishable** after migration `042`.

**Screen**: the "Publicación" drawer in `/expedientes/{id}`. Over a reserved expediente it does not offer publishing — and says why — but it **does** offer withdrawal, which is the backend's real asymmetry.

### `GET /api/v1/documents/{id}`

Get by UUID. Includes the list of anexos (attachments). **Access control (RF-SEG-08):** if the radicado exceeds the caller's clearance, it answers `404` (it does not reveal its existence).

### `GET /api/v1/documents/by-tracking/{tracking_number}`

Get by radicado number (e.g.: `2024-ICETEX-E-000001`). Same clearance scoping as the detail by UUID (`404` if it exceeds the caller's level).

### `PATCH /api/v1/documents/{id}`

Update `subject`, `dest_dept`, `observations`, `status`, `pages`, `doc_class` and `metadata`.
`tracking_number`, `doc_type`, `year`, `sequence` are immutable.
**Typing (`doc_class`) requires `PERM_RADI` at `crud >= 3`; the rest of the PATCH, `crud >= 1`.** The document type fixes the TRD series, and with it the radicado's retention and final disposition (Ley 594/2000 art. 24): an act with that consequence cannot be exercised with read permission. The threshold is only checked when `doc_class` is present in the body, so anyone correcting a subject or a page count keeps the level that always sufficed.
**Re-typing re-validates the metadata.** The effective metadata — whatever comes in the body, or whatever the radicado already had — is validated against the **new** type's active template before anything is written; if it does not fit, `422 metadata_validation_failed` and the radicado is left untouched. A document cannot end up typed as X while carrying the variable fields of Y. `metadata` is **replaced wholesale**, not merged, and the template version used is rewritten whenever re-validation happens — including the `NULL` when the new type has no active template — so the radicado does not keep pointing at the previous type's schema. Sending only `metadata` re-validates too: otherwise the registration-time validation would be bypassable with a later PATCH.
**Access control (RF-SEG-08):** `404` if the radicado does not exist **or** exceeds the caller's clearance. The gate is essential because the response returns the full radicado (subject, sender, attachments): without it, the write would also be a read.
**`409 radicado_anulado`:** an annulled radicado cannot be modified. `status` accepts `registered`, so without this guard a PATCH would undo an annulment approved through the two-step circuit, with no grounds and no approver. Un-annulling, if ever warranted, is an act of its own — not a side effect of editing a field. It is a `409` and not a `404` because at that point the radicado already passed the caller's clearance gate: saying it is annulled reveals nothing the caller could not already read.
**Audit:** writes a `document.radicado_actualizado` entry with the **before and after** of every field touched, in the SAME transaction as the mutation — if the entry fails, the change is rolled back.

### `PATCH /api/v1/documents/{id}/security-level`

**Reclassification of the security level** of an already-registered radicado (E08, parity with Orfeo's _Cambio Nivel de Seguridad_). Changes `nivel_seguridad` (1=PUBLIC, 2=RESERVED, 3=CLASSIFIED) without altering content or `tracking_number` (immutability intact). Requires the **`PERM_RECLASIFICAR`** permission.

- **Body**: `nivel_seguridad` (1..3, mandatory), `motivo` (non-empty string, **mandatory** — an act of reservation must be reasoned, Law 1712 arts. 19/28), plus optional `causal_reserva`, `fundamento_juridico`, `plazo_reserva_meses`.
- **Access control (RF-SEG-08)**: _no read-up_ — if the caller's clearance is below the radicado's current level it answers a neutral `404` (revealing neither existence nor level); _no write-up_ — it cannot set a level higher than the caller's own clearance (`403`). If the requested level equals the current one, it is an idempotent no-op (no audit entry, no event).
- **Trace**: records `nivel_anterior`, `nivel_nuevo`, `motivo` and the legal basis in the immutable `audit_log` (`document.radicado_reclassified`), atomically with the change.
- **Propagation**: emits `document.radicado.reclassified`; signature-service re-synchronises the `nivel_seguridad` snapshot of the object's signature chains (closing the RF-SEG-08 staleness residue).

---

## storage-service — port 8005

> Requires the `X-Tenant-Slug` header. Files stored in the MinIO bucket `orpycamcp-{slug}-documents`.

### `POST /api/v1/storage/upload`

Uploads a file (`multipart/form-data`, field `file`).

- Computes SHA-256; if it already exists in the tenant, returns the existing record with `200` (deduplication)
- Creates the tenant's bucket if it does not exist
- Returns `201` on a new upload
- **Validates the format** against the list of allowed MIME types (`ALLOWED_MIME_TYPES`, configurable; `*` = all) → `415 unsupported_media_type` if not allowed (RF-DIG-04)

```json
// Response 201
{
  "id": "uuid",
  "filename": "solicitud.pdf",
  "mime_type": "application/pdf",
  "file_size": 45231,
  "sha256": "e3b0c44298fc...",
  "uploaded_at": "2024-01-15T10:30:00Z"
}
```

### `GET /api/v1/storage/files/{file_id}`

File metadata (without content).

### `GET /api/v1/storage/files/{file_id}/download`

Returns a MinIO pre-signed URL valid for 1 hour.

```json
{
  "url": "http://minio:9000/orpycamcp-icetex-documents/uuid/solicitud.pdf?X-Amz-...",
  "expires_in": 3600
}
```

### `GET /api/v1/storage/files/{file_id}/verify`

Re-verifies integrity (RF-DIG-02): downloads the object from MinIO, recomputes the SHA-256 and compares it with the recorded one.

```json
{
  "file_id": "uuid",
  "expected_sha256": "...",
  "actual_sha256": "...",
  "integrity_ok": true
}
```

- `409 integrity_check_failed` if the object in MinIO does not match the recorded fingerprint (integrity alert).

### `POST /api/v1/storage/files/{file_id}/replace`

Replaces an anexo (attachment) by creating a **new immutable version** (RF-DIG-01; `multipart/form-data`, field `file`). The previous version is kept.

- `201` with the new version (`version` incremented); `200` if the content is identical (no version created); `409 not_current_version` if `{file_id}` is not the current version; `415` if the format is not allowed.

### `GET /api/v1/storage/files/{file_id}/versions`

Returns the version chain of the anexo (ordered by `version`). Each element includes `version` and `is_current`.

### `DELETE /api/v1/storage/files/{file_id}`

Deletes from MinIO and from the database. Responds `204`.

### Digital preservation (E10, OAIS/AGN 001/2024)

- `GET /api/v1/preservacion/plan` (current) · `PUT /api/v1/preservacion/plan` (new version) · `GET /api/v1/preservacion/plan/versions` — Versioned Digital Preservation Plan: `formatos_destino`, `num_copias`, `periodicidad_fixity_dias`, `politica_migracion`, `contingencia`.
- `POST /api/v1/preservacion/eventos` `{object_ref, tipo, resultado, hash_before?, hash_after?, detalle?}` · `GET /api/v1/preservacion/eventos?object_ref=` — immutable PREMIS log. `tipo` ∈ INGESTA/FIJACION/MIGRACION/VALIDACION_PDFA/WORM/REPLICA; `resultado` ∈ `ok`/`fallo`/**`no_evaluado`** (a third state since E10 C2a, migration `008`: PDF/A validation with the default stub honestly reports `no_evaluado` — _"not evaluated" ≠ "passed"_). When packaging the AIP, every **PDF** document (detected by the `%PDF` magic bytes) emits a `VALIDACION_PDFA` (**advisory** — a non-PDF/A PDF does not block the AIP) plus a PREMIS `validation` event. Config: `PRESERVATION_PDFA_VALIDATION_ENABLED` (default `false`), `PDFA_VALIDATOR` (`stub`|`verapdf`, default `stub`), `PDFA_PROFILE` (default `2b`). **The real veraPDF validator (subprocess) is implemented (E10 C2b)**: with `PDFA_VALIDATOR=verapdf` + `VERAPDF_BINARY` (absolute path to the binary, present in the **deploy** image — the dev image degrades honestly to the `no_evaluado` stub), `SubprocessVeraPdfValidator` runs veraPDF under a hardened security model (scrubbed ENV, guaranteed `killpg`+reap, semaphore, 0700 tempdir, timeout→`no_evaluado`, JSON-only). Tuning: `VERAPDF_TIMEOUT_SECONDS` (60), `VERAPDF_MAX_CONCURRENCY` (1), `VERAPDF_JVM_MAX_HEAP_MB` (512), `VERAPDF_JVM_MAX_METASPACE_MB` (256).
- `POST /api/v1/preservacion/aip` `{expediente_id, documentos:[{document_id, file_id, nombre}], indice_file_id?, retain_until? | (retencion_anios + fecha_inicio), legal_hold?}` — **(E10 Increment B, [ADR-023](adr/ADR-023-worm-object-lock-preservacion.md))** packages the **real OAIS AIP**: for each document it resolves the object by `file_id`, downloads the bytes from MinIO, **recomputes the fixity** (SHA-256 vs `files.sha256`; mismatch → `409` + a failed `FIJACION` event), assembles a **BagIt bag (RFC 8493)** (`data/` with the bytes + `bagit.txt` + `bag-info.txt` with a real Payload-Oxum + `manifest-sha256.txt` + `premis.xml` [PREMIS v3, official namespace, validated against a local XSD profile] + `indice-firmado.xml` [if `indice_file_id`] + `tagmanifest-sha256.txt`), **serialises it to ZIP**, uploads it to the preservation **WORM bucket** with `Retention(COMPLIANCE, retain_until)` derived from the TRD + `legal_hold`, and records it in `preservacion_worm_objeto` (`tipo='aip'` → covered by the disposition gate). Requires `USUA_PERM_EXPEDIENTE` with **`crud≥3`**. `nombre` is validated (no `/`, `..`, control chars) and must be **unique** within `documentos` (`422`). `201` with `{id, expediente_id, aip_sha256 (of the ZIP), num_documentos, estado, bucket, object_key, aip_bytes, formato, retain_until, retention_mode, legal_hold, premis_xml, replicado, replica_target, replica_copias_hechas, created_at}`; `400` invalid retention; `404/422` non-existent `file_id` or duplicate `nombre`; `409` fixity mismatch; `503` if `PRESERVATION_ENABLED=false`. **REPLICA (C1b)**: if `PRESERVATION_REPLICA_ENABLED=true` and the plan has `num_copias≥2`, after uploading the primary AIP an **immutable replica** is created in a second WORM bucket (`replica_target="local"` = a second bucket in the same MinIO, _not_ a geographic copy; `="cross-site"` if `MINIO_SECONDARY_ENDPOINT`). It is **best-effort and non-fatal**: a replica failure does **not** roll back the AIP (`replicado=false` + a `REPLICA` `preservacion_evento` with `resultado=fallo`); `num_copias≤1` → no replica.
- `GET /api/v1/preservacion/aip/{expediente_id}` — the current AIP (requires `USUA_PERM_EXPEDIENTE` `min_crud=1` — it exposes `premis_xml` with document names/hashes and the WORM location). The WORM location fields are `null` in AIP rows predating Increment B (backward compatibility).
- `POST /api/v1/preservacion/proteger-indice` `{file_id, expediente_id, retain_until? | (retencion_anios + fecha_inicio), legal_hold?}` — **(E10 Increment A, [ADR-023](adr/ADR-023-worm-object-lock-preservacion.md))** protects the **signed electronic index** (already uploaded to `files`, referenced by `file_id`) under **WORM / Object-Lock (MinIO)**. It provisions (idempotently) the per-tenant preservation bucket `orpycamcp-{slug}-preservation` (name taken **only from the JWT claim**), **verifies fixity** (recomputes the SHA-256 over the bytes and checks it against the hash **recorded at the original upload** — the caller declares **no** hash; mismatch → `409` + a failed `FIJACION` event, nothing is uploaded), copies the object with per-object **`Retention(COMPLIANCE, retain_until)`** + `legal_hold`, records it in `preservacion_worm_objeto` and emits `WORM`/`FIJACION` PREMIS events plus an `audit_log` entry. **Retention is supplied by the caller**, derived from the TRD (storage never calls archive): either `retain_until` directly **or** `retencion_anios` (1–100) + `fecha_inicio` (calendar-year arithmetic), never both. Requires `USUA_PERM_EXPEDIENTE` with **`crud≥3`** (the disposition threshold) + clearance. **Idempotent (E10 Inc.1 wiring)**: `201` with the WORM location on first protection; **`200`** with the existing row if that expediente's index is ALREADY protected (it does not re-upload — a short circuit before `put_object`, backed by the partial unique index `WHERE tipo='indice'` and a **per-`(tenant, expediente)` session advisory lock** that serialises `check→put→insert`, so no duplicate irreversible WORM objects are created under a race). `400` invalid retention (missing, in the past, out of bounds, or conflicting sources); `404` if the `file_id` does not exist in the tenant; `409` fixity mismatch; `503` if `PRESERVATION_ENABLED=false`. **Trust boundary**: the `file_id ↔ expediente_id` correspondence (that `file_id` really is that expediente's signed index) is guaranteed by **archive-service** (its only caller); storage validates that the `file_id` exists but does not model expedientes. **Effect on disposition**: while the index is under a future `retain_until` or a `legal_hold`, the expediente's final disposition stays **blocked** (destruction cannot happen before the retention expires — Law 594/2000, AGN Agreement 060/2001).
- _(Internal, service-to-service; **not** reachable from the gateway — `X-Internal-Token` is not in `FORWARDED_REQUEST_HEADERS`)_ `POST /api/v1/preservacion/proteger-indice-internal` — the **internal** twin (D-02) of `proteger-indice` with the SAME service and idempotency, authenticated **only** by `X-Internal-Token` instead of `require_permission(crud≥3)`, and attributed to the system principal `SYSTEM_RECONCILER_ID`. It exists because the **WORM wiring of the index** (`IndexService.proteger_worm_indice`, archive-service, E10 Inc.1) has two origins: the best-effort synchronous attempt right after signing the index on closure (`origen="close"`, a real user → the public route) and the **3rd reconciliation phase** (`run_once_worm`, `origen="reconciliation"`, `SYSTEM_RECONCILER_ID` with no `auth_users` row → this internal route; `require_permission` would always reject it). Retention is derived from the expediente's TRD (`archivo_gestion_years + archivo_central_years`, `fecha_inicio`=closure); **fail-closed if the TRD does not resolve** (it does not protect, marks `trd_sin_retencion`, recoverable by the sweep). It mirrors `/signature/internal/sign-indice`.
- `POST /api/v1/preservacion/proteger-artefacto` `{file_id, expediente_id, tipo, retain_until? | (retencion_anios + fecha_inicio), legal_hold?}` — **(E10 Inc.2 wiring, [ADR-023](adr/ADR-023-worm-object-lock-preservacion.md))** the **generalised** form of `proteger-indice`: it protects any **preservation artifact** of the expediente under WORM/Object-Lock, with `tipo ∈ {indice, acta_transferencia}` (defaults to `indice` if omitted). SAME service, fixity, caller-derived TRD retention, `USUA_PERM_EXPEDIENTE crud≥3` RBAC + clearance, and **idempotency** as `proteger-indice`, now keyed **on `(expediente_id, tipo)`** (partial unique index `WHERE tipo='acta_transferencia'` on `preservacion_worm_objeto`, migration `010`) and with the session advisory lock keyed on **`(tenant, expediente, tipo)`** — two artifact types of the same expediente (index and acta) do not collide, but each serialises its own `check→put→insert`. `/proteger-indice` is kept as a **hard alias** that forces `tipo="indice"` whatever the body says (Inc.1 backward compatibility). Status codes and trust boundary are identical to `proteger-indice`. The **signed transfer record** (XAdES seal, `transferencia_acta_firma`) is wired by archive via `TransferenciaService.proteger_worm_acta` (best-effort synchronous trigger after sealing + the 4th reconciliation phase `run_once_actas_worm`); its retention derives from the TRD of the transfer's expediente with **`fecha_inicio = firmado_at` of the acta** (not the closure date — an acta may be signed much later in secondary transfers).
- _(Internal, service-to-service; **not** reachable from the gateway)_ `POST /api/v1/preservacion/proteger-artefacto-internal` — the **internal** twin of `proteger-artefacto` (with `tipo` in the body), authenticated only by `X-Internal-Token`, attributed to `SYSTEM_RECONCILER_ID`. Used by the unattended acta reconciliation (`run_once_actas_worm`, `origen="reconciliation"`), just as `proteger-indice-internal` is for the index. `/proteger-indice-internal` remains the hard `tipo="indice"` alias.
- _(Internal, service-to-service; **not** reachable from the gateway — `X-Internal-Token` is not in `FORWARDED_REQUEST_HEADERS`)_ `POST /api/v1/preservacion/renovar-retencion-internal` `{expediente_id, tipo, retencion_anios + fecha_inicio | retain_until}` — **(E10 debt: WORM retention renewal for Permanent Conservation series)** extends (never shortens) the `retain_until` of an artifact **already protected** under WORM, identified by `(expediente_id, tipo)` (`tipo ∈ {indice, acta_transferencia}`). It reuses `RetentionMixin`. **Monotonic** semantics: if the resolved date does not **strictly** exceed the persisted `retain_until`, it is an **idempotent no-op** (`200`, `renovado=false`, MinIO untouched). If it does exceed it, it calls `Minio.set_object_retention` (a real extension; COMPLIANCE would reject a shortening anyway) and answers `200` with `renovado=true`; the UPDATE + the `RENOVACION` PREMIS event + the `file.worm_retencion_renovada` audit entry all go in **one transaction** (the `set_object_retention` sits outside it, before). Authenticated **only** by `X-Internal-Token` (attributed to `SYSTEM_RECONCILER_ID`): renewal is a **system act** driven by archive's 5th reconciliation phase (`run_once_worm_renovacion`), the only component that knows which series are Permanent Conservation — **there is NO public route** (it was withdrawn during remediation: storage cannot enforce the CT filter, so a public route taking a direct `retain_until` would be irreversible surface with no CT guarantee). `404` `worm_object_not_found`; `400` invalid retention; `503` if `PRESERVATION_ENABLED=false`. `retention_mode`/`legal_hold` do **not** change. Response: `{id, expediente_id, tipo, bucket, object_key, retain_until, retention_mode, renovado, renovaciones, renovado_at}`.
- `GET /api/v1/expedientes/indices/pendientes-renovacion?page=&size=` — **(E10 debt: visibility of CT renewal)** lists the WORM artifacts of **Permanent Conservation** series (index + acta) whose renewal is **exhausted** (`worm_renovacion_agotado=TRUE`) or **at risk** (`worm_retain_until` falls due within `worm_renovacion_ventana_dias`) — so that an archivist can see a permanent-conservation COMPLIANCE lock about to expire unrenewed (mirror of `pendientes-firma`). Gate `USUA_PERM_EXPEDIENTE` + **no read-up** (the `nivel_seguridad <= clearance` filter lives in the SQL of both queries; `X-Total-Count` reflects the count **already filtered** by clearance — it is not an oracle). Exhausted first, then nearest expiry. Per row: `{expediente_id, expediente_code, tipo, worm_retain_until, worm_renovacion_intentos, worm_renovacion_agotado, worm_renovacion_ultimo_error}`. The 5th reconciliation phase feeds it: for every active tenant it sweeps the indexes and actas **of Permanent Conservation (CT) disposition series** whose `retain_until` falls due within `WORM_RENOVACION_VENTANA_DIAS` (default 400 days) and calls the internal endpoint with `retencion_anios=TRD_total` + `fecha_inicio=today` (**rolling window**, never a far-off/9999 date). The CT filter is **fail-closed in archive's own SQL** (`disposition ∈ {CT, conserve}`) — `E`/`S`/`M` series never enter the sweep, even when close to expiry (they expire and are disposed of legitimately). It has its own exponential backoff (`worm_renovacion_intentos`/`worm_renovacion_agotado` in `expediente_indice`/`transferencia_acta_firma`, migrations `031`/`032`) — an `agotado=true` on this axis is a **higher-severity** alarm than on initial sealing/protection: it means the COMPLIANCE lock of a **permanently** conserved record risks expiring without ever having been extended. **AIP is out of scope** for renewal (no mirror in archive-service, future debt).

---

## workflow-service — port 8006

> Requires the `X-Tenant-Slug` and `X-User-Id` headers.

> **Automatic distribution (RF-FLU-04):** in addition to the endpoints, workflow-service consumes the `orpycamcp.document.events` stream. When a document is registered (`document.radicado.created`) it evaluates the active rules and, if any matches, **auto-assigns** the radicado to its dependencia (step + append-only event, author = system). It is idempotent (it does not redistribute if the radicado already has steps).

### `POST /api/v1/workflows/assign`

Assigns a radicado to a dependencia (first step or reassignment). Requires `PERM_TRAMITAR`; `403 forbidden` if the permission or the tenant is missing (RF-SEG-03).

```json
// Request
{
  "radicado_id": "uuid",
  "tracking_number": "2024-ICETEX-E-000001",
  "to_dept": "Dirección Jurídica",
  "assigned_to": "uuid-usuario",
  "action": "assign",
  "notes": "Para concepto jurídico"
}
// Response 201 — FlowStepResponse
```

### `POST /api/v1/workflows/transfer`

Transfers the radicado to another dependencia. Requires `from_dept`. Requires `PERM_TRAMITAR` (RF-SEG-03).

### `GET /api/v1/workflows/{radicado_id}/history`

Complete history of steps ordered by `step_number ASC`.

```json
{
  "radicado_id": "uuid",
  "tracking_number": "2024-ICETEX-E-000001",
  "steps": [
    { "step_number": 1, "from_dept": null, "to_dept": "Ventanilla", "action": "assign", "status": "completed", ... },
    { "step_number": 2, "from_dept": "Ventanilla", "to_dept": "Dirección Jurídica", "action": "transfer", "status": "pending", ... }
  ]
}
```

### `GET /api/v1/workflows/inbox`

Typed inbox of active steps (RF-FLU-03). `?box=entrada|salida|internos` (maps to `doc_type` E/S/I), `&dept=&assigned_to=&page=&size=`. Prioritizes by age; header `X-Total-Count`. `400 invalid_box` if the box is not valid. Each step includes `due_at` and `semaforo` (green/yellow/red/overdue, RF-FLU-07).

**Scoped by dependencia (RF-SEG-08):** without `dept`/`assigned_to`, the caller sees **their own** — steps assigned to them or belonging to a dependencia they are a member of (active URD). Passing `dept` and/or `assigned_to` **widens** the scope to another dependencia or user and requires `PERM_TRAMITAR` or `USUA_PERM_ADMIN`; without that permission, `403 forbidden` (the parameter is never silently ignored). `X-Total-Count` always reflects the same filter as the returned rows.

### `POST /api/v1/workflows/overdue/scan`

Overdue sweep (RF-FLU-07): detects active overdue steps not yet alerted, emits `workflow.step.overdue` to the bus (→ notifications) and marks them to avoid duplication. Idempotent per step; intended for periodic invocation by a scheduler. Responds `{ "alerted": N }`. Requires `USUA_PERM_ADMIN` (a tenant-wide sweep). The bulk reassignment `POST /api/v1/workflows/reassign/cascade` (RF-FLU-08) requires the same administrative permission.

### Sequential approvals (vistos buenos) (legacy parity)

- `POST /api/v1/workflows/{radicado_id}/vistos-buenos` `{revisores: [uuid,...]}` — creates the ordered review chain (1..20 reviewers, no duplicates). Requires `PERM_RADI` (D-09); `403 forbidden` if the permission, the identity or the tenant is missing. `409 cadena_existente` if the radicado already has a chain.
- `POST /api/v1/workflows/{radicado_id}/vistos-buenos/decidir` `{aprobar, comentario?}` — the reviewer whose turn it is (lowest pending order) approves/rejects. Requires a valid `X-User-Id` (400 if missing). `403 not_a_reviewer` if the actor is not in that radicado's reviewer chain; `403 no_es_su_turno` if they are in the chain but it is not their turn; a rejection stops the chain.
- `GET /api/v1/workflows/{radicado_id}/vistos-buenos` — chain + global status (`en_revision|aprobado|rechazado`).

### `POST /api/v1/workflows/{radicado_id}/devolver` (return to sender)

Re-routes a misassigned radicado: `{ "to_dept": "...", "causal": "DEV-DEP", "comentario"? }`. Closes the active step as `returned` and creates a new one towards `to_dept`, with the reason code in the history. Requires **`PERM_TRAMITAR`** (like `assign`, `transfer` and `complete`; this route and `rollback` were the only two trámite writes in the router with no gate at all). Authorization by tenure on top of the permission: only the current holder of the active step may return it. `403 not_current_holder` if the actor is not the holder. `404` if there is no active step.

### Procedure transactions (RF-FLU-01)

- `GET /api/v1/workflows/transaction-types` — catalog (informar, NRR, agendar, no_agendar, change_folder, marcar_leido, validate_trd_send, solicitar_firma, vobo, cerrar_exp, anular…), with their atomic permission and state effect.
- `POST /api/v1/workflows/{radicado_id}/transactions` `{ "tipo_tx": "anular", "comentario": "...", "detalles": {} }` — executes the transaction: applies the effect to the active step (e.g. `marcar_leido`→in_progress, `anular`→cancelled) and records the append-only event. **Per-type RBAC enforcement (RF-SEG-03):** each type declares in `transaction_types.requires_permission` the permission it demands (`anular`→`PERM_ANULAR`, `cerrar_exp`→`PERM_CERRAR_EXP`, `vobo`→`PERM_VOBO`…); without it, `403 forbidden` and the transaction is **not** applied. A type with `requires_permission = NULL` **falls back to the `PERM_TRAMITAR` floor**, it is not open: `transaction_types` is a tenant catalogue, and leaving the column empty cannot be the easiest way to open a door. All 12 seeded types declare their own, so the floor changes nothing observable today. There is deliberately no fixed route gate: requiring `PERM_TRAMITAR` for all of them would break someone who holds `PERM_VOBO` to sign off and nothing else. `404 unknown_transaction` / `no_flow`. `PATCH /{step_id}/complete` requires `PERM_TRAMITAR`.

### `POST /api/v1/workflows/{radicado_id}/rollback`

`{motivo}` (**mandatory**, 5-500 characters). Requires **`PERM_TRAMITAR`**. Reverts the last assignment (RF-FLU-08): cancels the current step —only if it is still `pending`— and reactivates the previous one, atomically, with a `tipo_tx=rollback` event. The `motivo` is recorded in two traces, atomically with the mutation (a single transaction): `flow_events.comentario` (the operational route map) and `public.audit_log.payload` (the forensic legal trace, the same pattern as assignment). `409 no_active_step` (no active step) or `cannot_rollback_initial` (it is the initial registration). `422` if the `motivo` is missing or below the minimum length.

### `POST /api/v1/workflows/reassign/cascade`

Reassigns, in a single transaction, all the active steps of a user or dependencia to a new responsible party, leaving no orphan radicados (RF-FLU-08).

```json
// Request (provide from_assigned_to or from_dept)
{ "from_dept": "Dirección Jurídica", "to_assigned_to": "uuid" }
// Response 200
{ "reassigned": 12 }
```

### `GET /api/v1/workflows/{radicado_id}/events`

**Append-only** history of the radicado's transactions (RF-FLU-02, unalterable). Unlike `/history` (a projection of steps), it records every transaction and its table rejects UPDATE/DELETE. **Access control (E05 §10 / RF-SEG-08):** this is the _operational route map_ (Channel B) — visible to any user who can **read** the radicado, scoped by their clearance (no read-up); requires `X-User-Id`, and a radicado above the clearance returns an empty history. No admin permission required (that is the `audit_log` forensic audit).

```json
[
  {
    "id": "uuid",
    "tipo_tx": "assign",
    "from_dept": null,
    "to_dept": "Ventanilla",
    "actor": "uuid",
    "comentario": "...",
    "detalles": { "step_number": 1 },
    "ts": "2024-01-15T10:30:00Z"
  }
]
```

### `GET /api/v1/workflows/{radicado_id}/current`

Current active step of the radicado. `404` if it is closed or unassigned.

### `GET /api/v1/workflows/pending`

Pending radicados. Filters: `?dept=Dirección+Jurídica&assigned_to={uuid}&page=1&size=20`.

**Scoped by dependencia (RF-SEG-08):** same criterion as `/inbox` — by default, one's own (assigned to the caller or to a dependencia they belong to through an active URD); `dept`/`assigned_to` widen the scope and require `PERM_TRAMITAR` or `USUA_PERM_ADMIN` (`403 forbidden` without the permission).

### `PATCH /api/v1/workflows/{step_id}/complete`

Marks a step as completed.

```json
// Request
{ "notes": "Concepto emitido, se adjunta" }
// Response 200 — updated FlowStepResponse
```

---

## archive-service — port 8004

> Requires the `X-Tenant-Slug` and `X-User-Id` headers.

### Expedientes

> **Access control:** **all** expediente endpoints (12: create/list/search/get/foliado/eventos/close/transfer/update/link/batch/unlink) and those in `batch.py` (`POST /api/v1/batch/expedientes`, `GET /api/v1/batch/expedientes/{job_id}/status`) require `USUA_PERM_EXPEDIENTE` (fail-closed: `403` without `X-Tenant-Slug`, without `X-User-Id`, or without the permission). **Least privilege (RF-SEG-03, `crud` level 0-5):** the **disposition** operations — `close`, `transfer`, `unlink`, and the `PATCH` that transitions to `closed`/`transferred` — require `USUA_PERM_EXPEDIENTE` with **`crud≥3`** (Create/Delete); everything else (reads, create, link, `PATCH` of name/series) is satisfied by `crud≥1`. In `list`/`get`/`eventos`/`search` **and in `PATCH`**, **clearance (RF-SEG-08, no read-up)** filters what is visible **in addition**, by security level: each user only sees/edits expedientes with `nivel_seguridad ≤` their clearance (_fail-closed_ to PUBLIC without `X-User-Id`), an expediente above it returns **404** (it does not reveal its existence), and in `list`/`search` the **`X-Total-Count` is filtered too** (it does not reveal how many classified ones exist). Every `list`/`search` query leaves an **aggregate trace** in `audit_log` (RF-BUS-10: user, criteria, total; without the raw text of `q`), separate from the per-expediente trail. In `link`/`batch`/`unlink`/`indice/rebuild` (M2, RF-SEG-08) the **mutation is legitimate without read clearance** (composing and ordering an expediente is archival management, orthogonal to reading its content) and it **always** happens and **always** audits; but the **response is trimmed** if the caller cannot read the expediente (`nivel_seguridad >` their clearance): counts/hash/`added_at` → `null`, and the **oracles are suppressed** (409 `radicado_already_linked`, 404 `radicado_link_not_found`, 409 `expediente_not_open`) by returning a **uniform, indistinguishable** ack whatever the state — a non-reader infers neither membership, nor volume, nor lifecycle of a classified expediente. The permission is resolved in the tenant DB (the api-gateway does not gate by route).

#### `POST /api/v1/expedientes`

Creates an expediente (case file). Generates an automatic code `EXP-{YEAR}-{SEQ:04d}`.

```json
// Request
{ "name": "Pensión María García - 2024", "description": "...", "trd_serie_id": "uuid",
  "metadata": { "tipo_contrato": "prestación" } }
// Response 201
{ "id": "uuid", "code": "EXP-2024-0001", "name": "...", "status": "open", "metadata": { ... }, ... }
```

- `metadata` (optional, ADR-007): if the TRD (document retention schedule) series (`trd_serie_id`) has an active template, the values are validated against its JSON Schema → `422 metadata_validation_failed` if they do not comply. Column indexed with GIN.

#### `POST /api/v1/expediente-metadata/templates`

Defines the metadata template (JSON Schema) of a TRD series. A single active one per series. `422 invalid_json_schema` / `409 conflict`. `GET` lists (filter `?trd_serie_id=`).

#### Documentary transfers (E12)

> **Access control (D-08):** all endpoints require `USUA_PERM_EXPEDIENTE`. Without it: `403 {"error": "forbidden"}`.

- `POST /api/v1/transferencias` `{tipo: primaria|secundaria, expediente_id, ubicacion_origen_id?, ubicacion_destino_id?}` — creates (precondition: expediente `closed`; `409 expediente_not_closed` otherwise). Cycle `preparada→enviada→recibida|rechazada`. Every act (create/send/receive/reject) leaves an immutable `audit_log` entry (both success and denial) with the actor identified unambiguously by `X-User-Id` (a UUID; no fallback to username). **Integrity (E12 H-L/H-M)**: at most **one active transfer** (`preparada`/`enviada`) per expediente — a second one gives `409 transferencia_activa_existente` (partial unique index; after a `rechazada` it can be re-prepared); and if `ubicacion_origen_id`/`ubicacion_destino_id` are supplied they must go **together** and be **coherent with the type** (`primaria`: origin `gestion`→destination `central`; `secundaria`: `central`→`historico`; Law 594/2000 art. 23), with `origen != destino` — otherwise `422` (`ubicaciones_par_incompleto` | `ubicaciones_origen_destino_iguales` | `ubicacion_not_found` | `ubicacion_incoherente_con_tipo`). **Mandatory only where there is physical custody**: if the expediente has a located physical placement (via `expediente_unidad`, which is M2M), the pair is **mandatory** (`422 ubicaciones_requeridas_fisico`) and `ubicacion_origen_id` must **check out** — it must be among the expediente's current locations (`422 ubicacion_origen_no_actual`; any of the M2M ones is valid); if the expediente is purely **electronic** (no physical units), the pair is optional (a logical transfer). The destination is not checked against anything (only type coherence — it is where it is going). Reading the physical topology needs no clearance (custody is orthogonal).
- `POST /{id}/enviar`, `POST /{id}/recibir`, `POST /{id}/rechazar`. On **receipt**, within a single transaction: the holdings' **fixity is re-verified** (SHA-256 of each document vs the index's hash + re-hash of the index XML; `409 fixity_mismatch` rolls back without freezing if the holdings were altered between closure and receipt — the response carries only `num_documentos_fallidos`, the per-document detail stays in `audit_log`), the expediente moves to `transferred` (frozen), and the **handover record (acta) is frozen** (see `/{id}/acta`). **Rejection** requires `{motivo}` (mandatory, `min_length=3`, non-empty → `422`); the reason is stored in its own column without overwriting the preparation note.
- `GET /api/v1/transferencias?estado=&tipo=`, `GET /{id}`, `GET /{id}/fuid` (the expediente's **live** FUID, regenerated on the fly).
- `GET /{id}/acta` (`?verify=true`) — the handover record **FROZEN** at receipt (E12 H-I, AGN Agreement 001/2024 FUID Annex; Law 594/2000 arts. 15/26). It returns the immutable snapshot written on receipt (it never regenerates it): a `TransferenciaActaResponse` with the canonical FUID XML, its `acta_fuid_sha256` (the acta's fixity, recomputable by the client), the three responsible parties `elaborado_por`/`entregado_por`/`recibido_por` with their dates, and the transferred `indice_version`. **`firma` block** (E06 H-H): `{presente, valida, xades_level, acreditado, motivo}` — the acta carries an **institutional XAdES-B/T/LT/LTA + OCSP seal** of the tenant (enveloped over the canonical XML, covering the very bytes whose SHA is anchored in `audit_log`); `presente=true` once sealing converged to `firmado`; with `?verify=true`, `valida` is computed on demand (it downloads the signed XML from MinIO and verifies it against signature-service). `acreditado` is **always `false`** (a non-ONAC-accredited seal — a **verifiable** timestamp, not strongly binding; `xades_level` must be read alongside `acreditado`). Sealing is **best-effort** (the signature is additive): if it has not been sealed yet, `firma.presente=false` with a `motivo` (`pendiente_firma`/`sello_ausente`/…) — the acta remains **intact** by fixity + immutability + anchoring. **No read-up** on reading (the acta exposes subject/series/signature = content): a full `404` if the expediente exceeds the caller's clearance — asymmetric with **generation**, which happens under unrestricted clearance (custody). The acta's immutability is **enforced by a trigger** at engine level (`BEFORE UPDATE/DELETE/TRUNCATE`, migration `023`), on top of being verifiable by fixity and anchored in the hash chain.
- `POST /{id}/acta/firmar` — **manual** retry of the XAdES sealing of an acta in `pendiente_firma` (E06 H-H). `USUA_PERM_EXPEDIENTE`, attributed to the human who triggers it (`origen="manual"`). `409 acta_ya_firmada` if already signed (idempotent), `503` if it still cannot be sealed. It **rearms** the reconciliation backoff so the automatic job resumes. The acta's signature also converges on its own via the generalised reconciliation job (at a lower cadence than the index).
- `POST /{id}/acta/firmar-personal` `{totp_code, rol?}` — **PERSONAL PKI electronic signature by a FUID RESPONSIBLE PARTY** (PREPARER "Elaborado por", role `elabora`; SENDER "Entregado por", role `entrega`; or RECEIVER "Recibido por", role `recibe`) over the handover record (E17/F4 Inc.1-3; AGN Agreement 001/2024 FUID Annex; AGN Agreement 042/2002; Law 527/1999 art. 7). An **explicit step after** `recibir()` (never inline: this preserves the custody transaction free of 2FA); the roles are signed **in any order**, each as an independent atomic act. **The role is DERIVED from the frozen identity**: `actor == creada_por` → `elabora`, `actor == enviada_por` → `entrega`, `actor == decidida_por` → `recibe`. The body's `rol` is **optional** and only breaks ties when the same identity holds several roles (self-transfer: `400 rol_requerido` if missing): it NEVER grants a role — a third party who is none of the three responsible parties gets **`403 firmante_no_es_parte`** (and likewise if they request via `rol` a part their identity does not confer), **before** the TOTP is consumed. `USUA_PERM_EXPEDIENTE` + real **no read-up** on the expediente for every role (a neutral `404` above clearance); `409 transferencia_no_recibida` if it is not `recibida` yet. Archive **mediates**: it forwards the responsible party's `Authorization`/TOTP to signature-service `POST /signature/sign-personal` (which verifies-and-consumes the TOTP atomically and signs the frozen `acta_fuid_xml` with their personal certificate). **Idempotent** over `(transferencia_id, rol)`: repeating an already signed role returns `200` with the existing row, without re-signing or re-burning a TOTP. `503 canal_firma_personal_no_disponible` if signature does not answer. Returns a `TransferenciaActaFirmaPersonalResponse` (`rol`, `firmante_id`, `firma_id`, `xades_level`, `acreditado` **always `false`**). Recorded in `transferencia_acta_firma_personal` (**without** unattended reconciliation — it requires an interactive TOTP). The `acta_firma_personal_require` gate (default `off`) **never blocks `recibir()`** nor another role's signature; it only modulates the compliance verdict.
- `GET /{id}/acta/verificacion` — **aggregate verification** of the acta (E17/F4 Inc.1-3). It composes the verification of the **institutional seal** plus that of **each personal signature** present (downloading the signed XMLs from MinIO and verifying them against signature-service). Returns `{sello: {...}, firmas_personales: [{rol, firmante_id, valida, revocacion_status}], completitud, valida, acreditado, auto_traslado, elaborador_firmado}`: `completitud ∈ {sellada, firma_personal_pendiente, firmada_receptor, firmada_remitente, firmada_completa}` (derived, a factual presence — `firmada_completa` = **both PARTIES TO THE HANDOVER** (sender + receiver) signed = **reinforced bilateral attribution**, NOT full legal conformity). The **PREPARER is an ADDITIVE signer** (not a party to the handover): their signature does NOT condition `completitud`, and is reported in the ORTHOGONAL boolean **`elaborador_firmado`**. `valida` = cryptography + revocation of **everything** present (including the preparer's signature, fail-closed → a revoked preparer certificate yields `valida=false` even with `firmada_completa`; the two orthogonality axes differ on purpose), `acreditado` is **always `false`**, and `auto_traslado=true` when the same identity both sent and received (**a compliance observation**: no dual control — the FUID does not require distinct natural persons, so it is flagged but not prevented). **No read-up** identical to `/{id}/acta` (a neutral `404` above clearance; the bodies never enumerate reserved radicados).

#### Physical archive (E17, ADR-017)

> **Access control (D-08):** all endpoints require `USUA_PERM_EXPEDIENTE`. Without it: `403 {"error": "forbidden"}`. `GET /expedientes/{id}/unidades` requires it too; the frontend calls it via `Promise.allSettled` and treats the 403 as a facet error without blocking the view.

- `POST/GET /api/v1/ubicaciones`, `GET /api/v1/ubicaciones/{id}` — recursive topology (site→…→drawer); `codigo` unique per tenant; derived `ruta`. Filters `?parent_id=&activo=`.
- `POST/GET /api/v1/unidades`, `GET /api/v1/unidades/{id}` — conservation units (box|folder|…); when `ubicacion_id` is provided, the **topographic signature** is derived (`DEP01-E05-C0124`, unique). Filters `?ubicacion_id=&tipo=`.
- `POST /api/v1/unidades/{id}/expedientes` — link an expediente to a unit (`folio_inicio/fin`).
- `GET /api/v1/unidades/{id}/expedientes` — what the unit contains; **filtered row by row** by clearance (RF-SEG-08, no read-up): enumerating a unit's contents reveals the _existence_ of its classified expedientes, so a non-reader does not see those with `nivel_seguridad >` their clearance (Law 1712/2014 art. 19; _fail-closed_ to PUBLIC, `403` without `X-User-Id`). `GET /api/v1/expedientes/{id}/unidades` — where the expediente is (signature + range); **deliberately without a clearance filter**: it is queried by an already known `expediente_id` and returns only the physical location (signature/folios, not subject/series) — custody management, orthogonal to read clearance (the same criterion as transfers).
- **Loans (RF-ARF-07)**: `POST /api/v1/unidades/{id}/prestamos` (409 if already lent out), `POST /api/v1/prestamos/{id}/devolver`, `GET /api/v1/prestamos?estado=prestado|devuelto|vencido`, `GET /api/v1/unidades/{id}/prestamos`. `vencido` is derived from `fecha_devolucion_esperada`.
- **History (RF-ARF-08)**: `GET /api/v1/unidades/{id}/movimientos` — append-only movements (RETIRADO/DEVUELTO/…).
- **FUID (RF-ARF-10)**: `GET /api/v1/fuid` — documentary inventory (expediente↔unit↔signature↔series) in JSON with consecutive ordering and AGN 042/2002 fields; filters `?ubicacion_id=&trd_serie_id=`. `GET /api/v1/fuid.xml` exports as XML. **Form header** (AGN Agreement 042/2002, Annex): `entidad_productora` is resolved **server-side** from `public.tenants` — it heads an official archival document, and a free-text field would turn one institution's inventory into any other's — while `unidad_administrativa`, `oficina_productora` and `objeto` arrive as query parameters and are **declarative**: whoever compiles the inventory fills them in, just as on paper. Every row also carries `frecuencia_consulta` (column 9 of the form: `alta`/`media`/`baja`), **derived** from access entries recorded in `audit_log` over the last 12 months rather than estimated — the count is scoped by `tenant_slug`, because `audit_log` is the only table shared across institutions and expediente codes are consecutives that collide between them. **Access control (RF-SEG-08, no read-up):** the FUID describes **content** (`asunto`/series/`signatura`), so it is filtered **row by row** by the expediente's classification — each user only sees in the inventory those with `nivel_seguridad ≤` their clearance (`total` counts only what is visible; _fail-closed_ to PUBLIC, `403` without `X-User-Id`). In `GET /transferencias/{id}/fuid` (a single expediente) the filter is a total `404` if the expediente exceeds the clearance (indistinguishable from non-existent). The transfer itself (create/send/receive/reject) is an act of **custody**, orthogonal to read clearance (it does not filter by it, as with expediente closure/transfer).

#### TRD/CCD — the TRD as a validated instrument (E04, [ADR-025](adr/ADR-025-trd-instrumento-convalidado-versionado.md))

> **Access control (archival configuration):** config **mutations** (`POST/PATCH /api/v1/trd`, `POST /trd/{code}/versiones`, the validation circuit below, `POST/PATCH/DELETE /api/v1/tipos-documentales`, `POST /api/v1/expediente-metadata/templates`) require **`USUA_PERM_TRD`** (fail-closed: `403` without tenant/user-id/permission) and leave an `audit_log` entry. **Config disposition** — `DELETE /tipos-documentales/{id}`, `PATCH /trd/{id}`, `POST /trd/{code}/versiones` and the validation circuit — requires **`crud≥3`** (RF-SEG-03). The `GET` **reads are not all alike**, and since 2026-08-28 the code says so (§4.bis of the test plan). The **classification vocabulary** — `GET /api/v1/trd`, `GET /trd/{id}`, `GET /trd/{id}/retention` and their TVD twins, plus `GET /api/v1/tipos-documentales` and `GET /api/v1/expediente-metadata/templates` — is **open to any authenticated user of the tenant, by declared decision**: `/radicar` and `/expedientes` consume it in order to classify, and `USUA_PERM_TRD` belongs to whoever **administers** the instrument, not to whoever uses it — requiring it here would stop every ordinary officer from registering — and the validated instrument must be published anyway (Ac. AGN 004/2019, compiled by 001/2024; Ley 1712/2014 art. 9). The **circuit** does require **`USUA_PERM_TRD`** (`crud≥1`): `GET /trd/revision-pendiente` and `GET /trd/{code}/versiones` + `GET /tvd/{code}/versiones`, because the history returns the drafts, the `acta_comite`, the `fecha_aprobacion_comite`, the `instancia_convalidante` and the `created_by` of every version — the Committee's deliberation, not the published instrument.
>
> **Instrument state machine** (ADR-025, AGN Agreement 001/2024 which compiles 004/2019): `borrador → aprobada → convalidada`, with `migrada` as a backfill state (series predating migration 037, "the system does not know whether they were validated") that can be **ratified** straight to `convalidada` without passing through `aprobada`, and `aprobada → borrador` (**return**, migration 038) as an honest path for returning with observations. `borrador`/`aprobada`/`derogada` do **not** govern a closure (`409 trd_version_no_vigente`); `convalidada`/`migrada` do. Effective destruction (final disposition, no job yet) will require the frozen snapshot to describe a validated instrument — `convalidada`, or `derogada` with an administrative act (D5, gate corrected in 038: see `app/core/trd_disposition.py`) — the rest of the rules live in the ADR.

- `POST/GET/PATCH /api/v1/trd` — documentary series and **subseries**. Fields: `code`, `name`, `parent_id` (subseries), `archivo_gestion_years`/`archivo_central_years` (two-phase retention), `disposition` (AGN: `CT`/`E`/`S`/`M`), `version`, `valid_from`, `estado`, `valid_to`, `acto_administrativo`, `acta_comite`, `fecha_aprobacion_comite`, `fecha_convalidacion`, `instancia_convalidante`, `rusd_radicado`, `fecha_publicacion`, `supersede_a`, `created_by`. `POST` always creates **`version=1` in `borrador`** (`estado` is not settable by the client). `PATCH` is **append-only** (D1): only applicable over `borrador` — any other state answers `409 trd_version_inmutable`, unless the only field sent is `pdfa_profile` (technical preservation policy, editable in any state). `is_active` is no longer accepted in `PATCH` (retiring a version is `derogar`, below).
- `POST /api/v1/trd/{code}/versiones` — clones the code's **current** version (`convalidada`/`migrada`) as a **new** row `version+1` in `borrador`; if the code has no current version (all its versions are `derogada`), it falls back to the **highest version number, in any state** (migration 038 — a derogated code with no successor is never left without a way back). `404 trd_code_not_found` only if the code never existed; `409 trd_version_en_tramite` if there is already a `borrador`/`aprobada` version in progress.
- `GET /api/v1/trd/{serie_id}/retention?closed_at=YYYY-MM-DD` — **retention calendar**: `fin_archivo_gestion`, `fin_archivo_central` and final disposition (`disposition_code`/`disposition_label`) derived from the series and the closing date, plus the **instrument's provenance** (`serie_version`, `serie_estado`, `acto_administrativo`, `fecha_convalidacion`, `instancia_convalidante`, `rusd_radicado`) — declared exactly as it stands in the row, never asserting a validation that did not happen (RF-FIR-15).
- `GET /api/v1/trd/{code}/versiones` — **(ADR-026; the twin declared by ADR-025 and never implemented)** the complete history (any state) of a code, most recent first; an empty list if the code never existed (not a `404`).

**Validation circuit (ADR-025 D2/D6, Increment 2 + migration 038)** — a state machine isolated in the service layer, validated before touching the DB (`409 trd_transicion_invalida` on any transition outside those listed):

- `POST /api/v1/trd/{code}/versiones/{version}/aprobar` `{acta_comite, fecha_aprobacion_comite}` — `borrador → aprobada` (approval by the Institutional Management and Performance Committee; both fields **mandatory**, migration 038 — previously the endpoint took no body and the system could not record the circuit's first act). A necessary and **insufficient** condition for governing a closure.
- `POST /api/v1/trd/{code}/versiones/{version}/devolver` `{motivo}` — **new (038)**: `aprobada → borrador`, with a mandatory `motivo`. The honest "returned with observations" path (AGN Agreement 001/2024) — previously the only way out of `aprobada` was `convalidar`, which demands declaring an act that may not exist yet. It clears `acta_comite`/`fecha_aprobacion_comite` (the approval becomes VOID).
- `POST /api/v1/trd/{code}/versiones/{version}/convalidar` `{acto_administrativo, fecha_convalidacion, instancia_convalidante, rusd_radicado?, fecha_publicacion?}` — **one action, two origins**: (a) from `aprobada` (the normal circuit, requiring `acta_comite`/`fecha_aprobacion_comite` to be already on record — `409 trd_aprobacion_comite_requerida` otherwise, migration 038): `aprobada → convalidada`, and **in the same transaction** it derogates the code's previously current version (if one existed: `valid_to=today`, `estado='derogada'`, `is_active=false`; if the derogation loses a concurrent race it aborts with `409` rather than leaving orphan FKs) and **re-points** (an `UPDATE`, not a clone, collision-safe — a key that already exists in the new version is **skipped**, never blowing up the transaction) the `tipos_documentales`/`expediente_metadata_templates` FKs from the derogated version to the new one; (b) from `migrada` (**D6 ratification**): `migrada → convalidada` **without creating a new version or derogating anything** — the same row gains the act's fields, and `trd_revision_requerida`/`trd_revision_motivo` are cleared on the `closed`/`transferred` expedientes bound to it whose reason is **exactly** `cerrado_bajo_serie_migrada` (other reasons, e.g. `serie_no_resoluble`, are not resolved this way). `acto_administrativo`/`fecha_convalidacion`/`instancia_convalidante` are mandatory in the body (`422` if missing; the `trd_series_convalidada_acto_check` CHECK requires them at DB level too); `rusd_radicado`/`fecha_publicacion` are optional. Once `convalidada`, the 038 trigger shields the act at ENGINE level (`acto_administrativo`/`fecha_convalidacion`/`instancia_convalidante` immutable, `estado` can only move to `derogada`).
- `POST /api/v1/trd/{code}/versiones/{version}/registrar-rusd` `{rusd_radicado?, fecha_publicacion?}` (at least one) — **new (038)**: records the RUSD registration and/or the publication date over a `convalidada` version — the regulation allows up to 30 business days AFTER validation to complete them, and before 038 the only moment the system accepted them was `convalidar`. Each field can only be filled **once** (NULL → value); `409 trd_rusd_ya_registrado`/`trd_fecha_publicacion_ya_registrada` if it already had a value — the 038 trigger shields this at engine level too.
- `POST /api/v1/trd/{code}/versiones/{version}/derogar` — `convalidada`/`migrada` → `derogada` (explicit retirement of the instrument, **with no successor**; `valid_to=today`, `is_active=false`). **Decision (038, finding #3 of the review):** blocked with `409 trd_derogacion_bloqueada_expedientes_abiertos` if any `open` expediente is classified under the code — derogating the only current instrument would leave them with nothing to govern their closure. It does not require an already validated successor (that requirement would block the legitimate use case of retiring a series that no longer produces documents); the code is never left without a way back, thanks to the `POST /versiones` fallback above. It does not affect expedientes already closed under this version (frozen snapshot, D3/D4).
- `GET /api/v1/trd/revision-pendiente` (`?page=1&size=50`, `X-Total-Count`) — report of expedientes with `trd_revision_requerida=true` (any reason: `cerrado_bajo_serie_migrada`, `cierre_sin_snapshot_de_retencion`, `serie_no_resoluble`), paginated. **No read-up (RF-SEG-08, corrected in 038):** it filters by `nivel_seguridad ≤ clearance` of the caller in both the `COUNT` and the page — previously any holder of `USUA_PERM_TRD` (a permission ORTHOGONAL to clearance) could enumerate RESERVED/CLASSIFIED ones; it also leaves an aggregate query trace (RF-BUS-10) when `X-User-Id`/`X-Canal` arrive. **Screen**: third tab of `/admin/trd` ("Revisión pendiente"), which distinguishes the three reasons by what resolves them — only `cerrado_bajo_serie_migrada` is cleared by validating the series; the other two have no automatic path, and the UI says so instead of offering a useless action.

#### `/api/v1/tvd` — Documentary Appraisal Schedules for accumulated holdings (E04, [ADR-026](adr/ADR-026-tvd-fondo-acumulado.md), Increment 1)

A TVD is **the same instrument as a TRD** (AGN Agreement 001/2024, which compiles 004/2019: the same Committee → Council → RUSD → publication circuit), modelled as a **`tipo_instrumento ∈ {TRD, TVD}` discriminator** over the same `trd_series` table — there is no separate table (ADR-026 D1: forking the `expedientes.trd_serie_id` seam, which feeds WORM COMPLIANCE `retain_until` at seven irreversible points, is the design the ADR rejects). Routes **mirror `/api/v1/trd` 1:1**, with the same **`USUA_PERM_TRD`** permission (the same archival authority, the same Committee — a separate permission would demand an RBAC migration for a distinction the regulation does not make), and `tipo_instrumento` is **set by the router, never accepted from the client**.

**What distinguishes a TVD** (its own fields, all **mandatory** on creation and **NULL by CHECK** in any TRD — `trd_series_tvd_forma_check`/`trd_series_trd_forma_check`, migration 039): `fondo_nombre` (the producing entity, often **defunct**), `fecha_extrema_inicial`/`fecha_extrema_final` (the holdings' extreme dates — a TVD has no **records-management phase**, so `archivo_gestion_years` is always `NULL`/`0`), and `justificacion_valoracion` (the **object** of the instrument — the reasoning that would be cited in a destruction record, RF-FIR-15). The `code` namespace is **unique and shared** with the TRD (Layer 1 of the ADR): registering a TVD with a code already used by a TRD answers `409 instrumento_code_en_uso`, naming the occupant's type.

**The property that defines Increment 1: a TVD does not yet govern any expediente.** The binding trigger (`trg_expedientes_pin_tipo`) rejects in the engine **any** expediente `trd_serie_id`/`trd_id` pointing at a TVD row — WORM risk is **zero**, guaranteed by construction, not by omission. An institution can register, approve, validate, register in the RUSD and publish its TVD (which was the original blocker) without there yet existing any route by which it governs a retention computation. Increment 2 (outside this increment) relaxes the trigger to `expedientes.origen ↔ tipo_instrumento` and gives `compute_retention` an explicit base date (`fecha_extrema_final`, never `closed_at` — D4 of the ADR).

- `POST /api/v1/tvd` `{code, name, description?, parent_id?, disposition, total_retention, archivo_central_years?, pdfa_profile?, fondo_nombre, fecha_extrema_inicial, fecha_extrema_final, justificacion_valoracion}` — creates the grouping as **version 1 in `borrador`**. It does **not** accept `archivo_gestion_years`, `estado` or `tipo_instrumento`. `409 instrumento_code_en_uso` if the code already belongs to another instrument.
- `GET /api/v1/tvd?page=&size=&is_active=` — reads **`v_tvd_agrupaciones`** (never the base table): the isolation between TRD and TVD lives in the `FROM`, not in a forgettable `WHERE`. `X-Total-Count`.
- `GET /api/v1/tvd/{id}` — `404` if the id exists but is a TRD (it reads the view: it is not "filtered out", it is simply not there).
- `PATCH /api/v1/tvd/{id}` — `borrador` only (+ `pdfa_profile` in any state), the same append-only gate as TRD (`409 trd_version_inmutable`).
- `POST /api/v1/tvd/{code}/versiones` — clones the current one as `borrador` `version+1`.
- `GET /api/v1/tvd/{code}/versiones` — complete history, most recent first.
- `POST /api/v1/tvd/{code}/versiones/{version}/aprobar` `{acta_comite, fecha_aprobacion_comite}` — `borrador → aprobada`.
- `POST /api/v1/tvd/{code}/versiones/{version}/devolver` `{motivo}` — `aprobada → borrador`.
- `POST /api/v1/tvd/{code}/versiones/{version}/convalidar` `{acto_administrativo, fecha_convalidacion, instancia_convalidante, rusd_radicado?, fecha_publicacion?}` — `aprobada → convalidada` (derogates the code's previous current version in the same transaction, re-pointing orphan FKs). **Unlike TRD, the only origin is `aprobada`**: a TVD never reaches `migrada` (it is born in `borrador`; ADR-025's D6 ratification does not apply — the TVD population starts empty).
- `POST /api/v1/tvd/{code}/versiones/{version}/registrar-rusd` `{rusd_radicado?, fecha_publicacion?}`.
- `POST /api/v1/tvd/{code}/versiones/{version}/derogar` — `convalidada → derogada`.

The audit entry names are **deliberately different** (`archive.tvd_*`, object `tvd_agrupacion`): in `audit_log` the decision is the opposite of the one taken in the data model — whoever filters on `archive.trd_version_convalidada` should not have to remember to also inspect a payload field.

#### `POST /api/v1/expedientes/{id}/radicados/batch` (bulk inclusion)

Links several radicados at once `{radicados:[{radicado_id, tracking_number, content_hash?, formato?, tamano_bytes?, folios?, dependencia_productora_codigo?, dependencia_productora_nombre?}]}` (up to 500; idempotent, skipping those already linked — it does not update the metadata of already-linked ones); regenerates the index only once. The RT-15 fields (`formato`/`tamano_bytes`/`folios`) and the **producing dependencia** (code + name, v4) are optional but **code and name must go together or not at all** (Pydantic `422` validation, mirroring `RadicadoLink`). Returns `{vinculados, omitidos, total}`. `409` if the expediente is not `open` (for a caller without read clearance over a classified expediente, `vinculados`/`omitidos` arrive as `null` and the `409` is replaced by the trimmed ack — see the access-control note above).

#### Expediente lifecycle (E02)

- `POST /api/v1/expedientes/{id}/close` — closes the expediente (`open→closed`), materializes the TRD disposition in `disposition` and **generates and signs (XAdES-B) the final version of the electronic index** via signature-service (E06 Inc.1). Sealing is **best-effort and does not block the closure**: if the signature is obtained it leaves `expediente_indice.estado='firmado'`; if it fails (seal not provisioned, service unreachable) it leaves **`estado='pendiente_firma'`** and emits the `archive.indice_firma_pendiente` audit entry. The closure audit entry includes `indice_estado`/`firma_id`. **Response**: a **trimmed** `ExpedienteDisposicionResult` (`id`, `code`, `status`, `disposition`, `closed_at`, `transferred_at`) — **not** the full expediente: disposition is orthogonal to read clearance (RF-SEG-08, Option B), so the response exposes no `metadata`/`description`/`radicados`/`opened_by`, and a user with disposition authority (`crud≥3`) but no clearance over a CLASSIFIED expediente receives **200** (previously: a spurious 404 despite closing successfully).
- `POST /api/v1/expedientes/{id}/indice/firmar` — **retries** the XAdES-B sealing of an index in `pendiente_firma` (E06 Inc.1). Idempotent: `409` if already `firmado`, `503` if sealing still cannot be obtained. Requires `USUA_PERM_EXPEDIENTE` **+ `PERM_FIRMA`** and `X-User-Id` (400 if the UUID is malformed).
- `POST /api/v1/expedientes/{id}/transfer` — transfers (`closed→transferred`). `409` if the transition is invalid **or if the index is not `firmado`** (an index in `pendiente_firma` blocks the transfer — AGN Agreement 001/2024 art. 4.3.2.4). The same block applies in the generic `PATCH` with `status=transferred` and on receipt of transfers (E12). **Response**: a trimmed `ExpedienteDisposicionResult` (as with `/close`), not the full expediente.
- `GET /api/v1/expedientes/{id}/eventos` — **append-only route map of the expediente** (E02 §9, RF-EXP-09): chronological list of `{id, accion, actor, comentario, detalles, ts}` with `accion` ∈ `abierto|radicado_vinculado|radicado_excluido|cerrado|transferido|...`. **Access control (Channel B / RF-SEG-08):** visible to anyone who can **read** the expediente, scoped by their clearance (no read-up); no admin required. Distinct from `audit_log` (forensic audit) and `/unidades/{id}/movimientos` (physical movements, E17).
- `GET /api/v1/expedientes/{id}/foliado` — foliation: radicados ordered with a sequential `folio` (`{expediente_id, code, total_folios, items[]}`). (The normative **electronic index** is E15, below.)

#### Electronic index (E15, AGN Agreement 001/2024)

> **Profile v4 "fully conformant" (E15, [ADR-022](adr/ADR-022-indice-electronico-v2-metadatos.md); XSD `schemas/indice-v4.xsd`):** new indexes are generated in `urn:orpycamcp:indice:v4`, which **closes the 4 residues of v3**. **Per document**: RT-15 metadata (`<formato>`/`<tamano>`/`<folios>`/`<fechaIncorporacion>`), a **stable `<orden>`** (immutable), **`<dependenciaProductora codigo="N">`** (supplied at link time; **omitted** on legacy rows without the datum) and **`<fechaDeclaracion>`** (= `added_at`, declaration ≡ incorporation). **In the header**: `<contexto>` (`fechaPrimerUso` + series/subseries by name), **`<listaControlAcceso>`** with `<nivelSeguridad>` + **`<politicaAcceso modelo="no-read-up" requiere="USUA_PERM_EXPEDIENTE" clearanceMinimo="N">`** (a stable, portable policy — the **live RBAC matrix is NOT embedded**, it is queried through an endpoint), **`<pistaAuditoria registro="public.audit_log" objetoRef cadenaVerificable>`** (it attests to the trail, it does **not** embed live entries) and **`<conformidad perfil="v4" productoraPresente pistaAccesoDesde>`** (a per-instance marker; `productoraPresente="false"` over an empty set). The v4 **content_hash canonicalises the whole tree** (v3 covered only `<documentos>`). **Excluding** a radicado is a **soft delete** (`<documento excluido="true">` with its ordinal + `<fechaExclusion>`/`<causalExclusion>` → a **traceable gap**, AGN 001/2024 art. 4.3.2.3). Indexes in v1/v2/v3/v4 that are **already signed remain immutable** (`verify` is version-agnostic). **Label**: "fully conformant — **prospective applicability**" (legacy instances may omit the producing dependencia and their access trail starts at instrumentation; every index self-declares this in `<conformidad>` — RF-FIR-15, do not over-declare). **All** index reads require `USUA_PERM_EXPEDIENTE` + clearance (no read-up, RF-SEG-08) and **leave an access entry** in `audit_log` (`archive.indice_consultado`, real actor only + channel `api|ui|mcp`).

- `GET /api/v1/expedientes/{id}/indice` (`?version=N`) — current index or by version: `{version, estado, num_documentos, xml_sha256, algoritmo, items[]}`. Each item includes `formato`, `tamano_bytes`, `folios`, `fecha_incorporacion` (null if not declared at link time).
- `GET /api/v1/expedientes/{id}/indice.xml` (`?version=N`) — XML body (`application/xml`, namespace `urn:orpycamcp:indice:v4` on new indexes; `v1`/`v2`/`v3` on already-signed ones).
- `GET /api/v1/expedientes/{id}/export.zip` — **export of the expediente to ZIP** (E02/export, Law 594/2000 art. 19). A CONSULTATION/DELIVERY copy (distinct from the E10 preservation AIP and from the E12 FUID transfer). The ZIP contains: `indice-electronico.xml` (E15, if signed — best-effort), `manifiesto.csv` (both **linked and excluded** radicados —the latter with their reason, without bytes—, with `orden_documental`/`folios`/`formato`/`dependencia_productora`/`fecha`), `documentos/{tracking}/{idx}-{filename}` (the attachment bytes), `LEEME.txt` (a scope legend) and `checksums.txt` (SHA-256 per member, to validate integrity). Requires `USUA_PERM_EXPEDIENTE`. **PER-RADICADO no read-up** (RF-SEG-08): `404` if the expediente exceeds the caller's clearance, and **individual radicados above the clearance are omitted entirely** (not even their existence is enumerated in the manifest) — an expediente-level gate is not enough. Archive orchestrates; the byte assembly is done by storage-service (an internal D-02 endpoint, unreachable from the gateway) without persisting anything; `arcname` is sanitised against zip-slip. The export leaves an aggregate audit entry (`archive.expediente_exportado`, RF-BUS-10). _Debt: combined PDF, streaming for large expedientes, `asunto`/document type in the manifest._
- `GET /api/v1/expedientes/{id}/historial-acceso` (`?page&size`, E15 v4 R3) — the expediente's **access trail**: it reads `public.audit_log` (read-only, with an explicit `tenant_slug` filter) filtered by `object_ref = code` and `object_type ∈ (expediente, expediente_indice)`, ordered `ts DESC`, with `X-Total-Count`. Returns `{ts, actor, canal, action, ...}`. Requires `USUA_PERM_EXPEDIENTE` + no read-up (**404**, not 403, if the expediente exceeds the caller's clearance — it does not leak existence).
- `GET /api/v1/expedientes/{id}/roles-autorizados` (E15 v4 R4) — a **live snapshot** (unsigned) of the access matrix: the groups that hold `USUA_PERM_EXPEDIENTE` (`crud≥1`) **and** `role_clearance.max_level ≥` the expediente's `nivel_seguridad`, as `[{grupo, clearanceMax, crud}]` sorted by name. Same gate (`USUA_PERM_EXPEDIENTE` + no read-up). This is the datum the signed index deliberately does **not** embed (avoiding churn in the evidentiary artifact and IAM topology leakage in AGN transfers).
- `GET /api/v1/expedientes/{id}/indice/versions` — version history (append-only) with `X-Total-Count`.
- `GET /api/v1/expedientes/{id}/indice/verify` (`?version=N`) — verifies integrity: it checks the index's `valor_huella` against each document's current hash **and** re-hashes the stored XML against `xml_sha256` (`huella_indice_ok` — detecting alteration of the index XML itself, even unsigned). With an XAdES-B signature it downloads the signed XML from MinIO and verifies it against signature-service — the `firma` block includes `{presente, valida, nivel_conformidad, acreditado, motivo}` (`motivo='signed_xml_unavailable'` → `valida=false`). `valido` is the conjunction of per-document fixity + `huella_indice_ok` + the signature (if present). Requires `USUA_PERM_EXPEDIENTE`.
- `GET /api/v1/expedientes/indices/pendientes-firma` — an operational list (paginated, `X-Total-Count`) of the indexes in `pendiente_firma` belonging to `closed`/`transferred` expedientes, with their reconciliation counters (`reconcile_intentos`, `reconcile_agotado`, `reconcile_ultimo_error`, version, `expediente_code`) — direct visibility for the archivist of indexes not yet sealed, **including the `reconcile_agotado=true` ones** that need human intervention (provision the seal + manual retry). **No read-up**: filters by `nivel_seguridad <= clearance` of the caller (in both `COUNT` and rows), so a low clearance does not see indexes of classified expedientes. Requires `USUA_PERM_EXPEDIENTE` (E15/E06 Inc.8).
- `POST /api/v1/expedientes/{id}/indice/firmar` — **manual** retry of the XAdES sealing of an index in `pendiente_firma` (requires `USUA_PERM_EXPEDIENTE`+`PERM_FIRMA`, attributed to the human). `409 indice_ya_firmado` if already signed (idempotent), `503 index_signing_failed` if it still cannot be sealed. A manual retry **rearms** the reconciliation backoff (`reconcile_agotado`→false) so the automatic job resumes once the root cause is fixed. The `archive.indice_firmado` entry carries `origen="manual"`.
- `POST /api/v1/expedientes/{id}/indice/rebuild` — regenerates (a new version if the set or the metadata changed; `{unchanged:true}` if identical). **`409 expediente_not_open`** if the expediente is not `open`. **Version 0** is created automatically when the expediente is created. Requires `USUA_PERM_EXPEDIENTE`. Each regeneration leaves an `archive.indice_regenerated` entry (with `xsd_version`) in `audit_log`.
- When linking a radicado (`POST /api/v1/expedientes/{id}/radicados`) you may supply `content_hash` (SHA-256, `valor_huella`), the RT-15 metadata **`formato`** (MIME), **`tamano_bytes`** and **`folios`** (optional, `ge=0`) and, in **v4**, the **`dependencia_productora_codigo`** (INTEGER) + **`dependencia_productora_nombre`** (text) — optional but **together or not at all** (`422` on the partial case). An immutable snapshot is persisted per index version, so `GET /indice?version=N` shows the producing dependencia as it stood when that version was generated. Linking/excluding regenerates the index automatically; on a non-`open` expediente it returns `409`.

#### `GET /api/v1/expedientes/search` (FTS search, E09)

Full-text search of expedientes (ADR-014). `q` optional (`tsvector` over `code`/`name`/`description` + `ts_rank` ranking); filters `?status=&date_from=&date_to=&meta.<campo>=`. Header `X-Total-Count`. Without text = filtered query ordered by `opened_at`. **Access control (RF-SEG-08 / RF-BUS-04):** only expedientes with `nivel_seguridad ≤` the caller's clearance are returned (_fail-closed_ to PUBLIC). The level is set when creating the expediente (`POST /api/v1/expedientes`, field `nivel_seguridad`, default 1).

#### `/api/v1/tipos-documentales` (3rd TRD level, RF-MET-07)

CRUD for the catalog of documentary types (`POST`/`GET`/`GET {id}`/`PATCH {id}`/`DELETE {id}`). Fields: `code`, `nombre`, `trd_serie_id` (series/subseries), `obligatorio`, `metadata_template` (associated template). Filters `?trd_serie_id=&activo=`. `409` if the `(serie, code)` already exists.

#### `GET /api/v1/expedientes`

List with filters `?status=open&page=1&size=20`.

#### `GET /api/v1/expedientes/{id}`

Get by UUID. Includes the list of linked radicados. **Access control (RF-SEG-08):** if the expediente exceeds the caller's clearance, it answers `404`.

#### `PATCH /api/v1/expedientes/{id}`

Update `name`, `description`, `trd_serie_id`, `status`.
Valid transitions: `open → closed → transferred`. It cannot be reopened.

#### `POST /api/v1/expedientes/{id}/radicados`

Link a radicado to the expediente.

```json
{ "radicado_id": "uuid", "tracking_number": "2024-ICETEX-E-000001" }
```

#### `DELETE /api/v1/expedientes/{id}/radicados/{radicado_id}` (`?causal=…`)

Excludes (unlinks) a radicado. It is a **soft delete** (E15 v3): it marks `estado='excluido'` (+ `excluido_at`, `excluido_causal` from the optional query param) — it does **not** delete the row; the `orden_documental` is never reused and the index keeps the gap traceable. `204`. Requires `USUA_PERM_EXPEDIENTE` with `crud≥3` (disposition). `404` if it was not linked.

### TRD

> See the **"TRD/CCD — the TRD as a validated instrument"** section above (§ archive-service) for the complete, up-to-date contract (ADR-025: append-only versioning, state machine, validation circuit). The examples here remain only as a quick reference for the shape of the body.

#### `POST /api/v1/trd`

Create a documentary series (always `version=1`, `estado='borrador'`).

```json
{
  "code": "100",
  "name": "Contratos",
  "retention_years": 5,
  "total_retention": 10,
  "disposition": "conserve"
}
```

#### `GET /api/v1/trd`

List series. Filter: `?is_active=true&page=1&size=50`.

#### `GET /api/v1/trd/{id}`

Get series by UUID.

#### `PATCH /api/v1/trd/{id}`

Update a series — only applicable if `estado='borrador'` (append-only, ADR-025 D1); in any other state use `POST /trd/{code}/versiones`.

---

## notification-service — port 8007

### `POST /api/v1/notifications/send`

Manual sending of an email notification. Responds `202` (accepted for async sending). The history is persisted in the database of the indicated `tenant_slug`.

```json
{
  "recipient_email": "juridica@icetex.gov.co",
  "subject": "Nuevo radicado",
  "body": "...",
  "tenant_slug": "icetex"
}
```

### `GET /api/v1/notifications/history`

Notification history of the tenant (persisted in the database per institution). Requires `X-Tenant-Slug`. Query: `?limit=20`.

### `GET /api/v1/notifications/{id}`

Get notification by UUID. Requires `X-Tenant-Slug`. `404` if it does not exist.

### Electronic signature — signature-service, port 8008 (E06, ADR-016)

A **pluggable** provider (`app/providers/`): `nativa` (HMAC hash+identity, the default for every object) and **`xades_local`** (real enveloped XAdES-B via `signxml`, active only for the **electronic index**). Selection: `xades_local` when `objeto_tipo=="indice"` **and** `SIGNER_PROVIDER=="xades_local"` (the default; kill switch `SIGNER_PROVIDER=nativa`); anything else → `nativa`. See [ADR-016 addendum Inc.1](adr/ADR-016-firma-electronica.md#addendum--incremento-1-2026-07-02-xades-b-del-índice-electrónico--sello-institucional).

- `POST /api/v1/signature/sign-xml` `{payload_xml, objeto_tipo, objeto_id?, formato?, indice_id?, version?}` — signs the payload and persists the signature. With `objeto_tipo="indice"` it produces **XAdES-T** (XAdES-B with the tenant's institutional seal **+ an RFC 3161 timestamp** from a TSA, E06 Inc.4) and, if `SIGNER_LT_ENABLED=true` **and** the tenant's seal carries CA material (`ca.crt`+`crl.der`), it escalates to **XAdES-LTA** (E06 Inc.5): it adds `xades:CertificateValues` (the CA only) + `xades:RevocationValues` (the CRL) + `xadesv141:ArchiveTimeStamp`. With `SIGNER_OCSP_ENABLED=true` (default `false`) **and** the OCSP responder pair mounted (`ocsp.crt`+`ocsp.key`, per tenant alongside the seal), it additionally embeds a **stapled OCSP response (RFC 6960)** as `xades:OCSPValues/EncapsulatedOCSPValue`, a **sibling** of the CRL inside the same `RevocationValues` (E06 Inc.7) — signed by a **delegated responder** issued offline by the tenant's CA, with `certStatus` derived from the CRL, and covered by the `ArchiveTimeStamp`. Requires `PERM_FIRMA` + `X-User-Id` (the signer **never** comes from the body, RF-FIR-11). Returns `{firma_id, payload_sha256, provider, minio_key_firmado?, xades_level?, nivel_conformidad?, tipo_firma?, certificado_fingerprint?, acreditado, tsa_timestamp?, lt_material_present?, archive_timestamp_present?, crl_next_update?, revocation_provenance?, ocsp_present?, ...}`. `xades_level` ∈ `XAdES-B|XAdES-T|XAdES-LT|XAdES-LTA` according to what was **achieved** (never what was intended; OCSP does **not** change the level, it enriches the revocation evidence); **without CA material the signature stays at XAdES-T** and leaves a `firma.lt_downgrade_no_ca` audit entry; **with OCSP enabled but no responder pair** it leaves `firma.ocsp_downgrade_no_responder` (staying at LT/LTA with the CRL alone). `acreditado` is **always `false`** (dev CA/TSA/OCSP responder, not ONAC — validation material that is **verifiable, not accredited**, RF-FIR-15; `nivel_conformidad` ∈ `xades_b|xades_lt|xades_lta_no_acreditado`, dev revocation marked with `revocation_provenance="dev"`). `503 signing_seal_unavailable` if the seal is missing; **`503 timestamp_authority_unavailable`** if the TSA is unavailable; **`503 lt_material_required`** if `SIGNER_LT_REQUIRE=true` and CA material is missing; **`503 ocsp_material_required`** if `SIGNER_OCSP_REQUIRE=true` and no OCSP response was produced (missing CA or responder material); `503 signed_index_upload_failed` if storage rejects the upload.
- `POST /api/v1/signature/sign-personal` `{payload_xml, objeto_tipo, objeto_id?, totp_code, firmante_id?}` — **personal PKI signature of an arbitrary XML payload** with the authenticated actor's certificate (E17/F4 Inc.1; the `personal_signing.sign_personal_xml` primitive is reused by the radicado chain and by the transfer acta signature). Authenticated as a **user** (`get_actor` + `PERM_FIRMA`, `X-User-Id`/JWT — **not** `X-Internal-Token`: it carries the real signer's identity + TOTP, unlike `/internal/sign-indice`). **ASSERT `firmante_id == user(JWT)`**: if the body carries `firmante_id` and it differs from the actor derived from the token → `403 firmante_no_coincide` (not impersonable through the body; the persisted row always uses the JWT actor). It assembles the XAdES with an ephemeral RSA key + the real public certificate, asks auth-service to sign the `SignedInfo` digest **while consuming the TOTP atomically** (the remote-signing split), and uploads the signed XML to MinIO under a deterministic name that includes the `firma_id` (the H-H pattern, no overwrite under a race). It returns the standard `FirmaResponse` (`firma_id`, `minio_key_firmado`, `xades_level`, `provider="xades_personal"`, `acreditado` **always `false`**, `nivel_conformidad='xades_personal_no_acreditado'`). **The no-read-up gate no longer hangs off `objeto_tipo`**: `objeto_tipo` is free text here (it has to be — institutional actas travel this route), and while the gate hung on `== "radicado"`, declaring any other value over a CLASSIFIED radicado's identifier evaluated it against level 1 and skipped it. The level is now resolved from the `objeto_id` against `radicados`/`expedientes`, whatever type is declared; if it does not resolve, it is an object with no level of its own (an acta, a draft) and stays at PUBLIC, and for those the no-read-up is still enforced by the **mediating service** (archive) **before** calling — verified that a direct call is not a bypass (it only signs the payload the caller itself supplies, it does not insert into the domain tables). `4xx` if the TOTP is invalid; `503` if the auth channel is down.
- `POST /api/v1/signature/verify` `{firma_id, payload_xml}` — verifies. **`xades_local` signatures**: `payload_xml` must be the **complete signed XML** (the one at `minio_key_firmado`); it validates cryptography + root coverage + certificate validity + seal fingerprint + a match against `signed_xml_sha256`; **XAdES-T** additionally validates the RFC 3161 token (covering the `ds:SignatureValue`, CMS signature, TSA cert **pinned to `firma.tsa_cert_fingerprint`**); **XAdES-LT/LTA** additionally validates (fail-closed) the **`seal→CA` chain** (anchored to **the tenant's** CA via `ca_fingerprint` pinning — a seal from another tenant's CA fails), the CRL's **signature and non-revocation** (the seal's serial not revoked), and the **`ArchiveTimeStamp`** over the deterministic covered set; `crl_next_update` is exposed without a hard fail if it has lapsed (not accredited). If the signature carries **`xades:OCSPValues`** (E06 Inc.7), it additionally validates (fail-closed) the **OCSP response (RFC 6960)**: `responseStatus=successful`, responder issued by the tenant's CA + validity, `OCSPSigning` EKU + `id-pkix-ocsp-nocheck`, the `BasicOCSPResponse` signature, a **CertID bound to the seal** (serial + issuer hashes), `certStatus=good`, and **pinning** of the responder fingerprint against `firma.ocsp_responder_fingerprint` (without it → fail-closed); its absence is backward-compatible (LT valid on the CRL alone). **`xades_personal` signatures** (per-user personal PKI signature, E06/E17 Inc.1-2): it validates cryptography + coverage + validity + the leaf fingerprint; if the signature embeds the sub-CA (`xades:CertificateValues`, Inc.2) it additionally anchors the **leaf→tenant user sub-CA chain** with `user_ca_fingerprint` pinning (a branch separate from the seal's — it does not require an embedded CRL); and it **checks revocation online** (state in the DB, no cache) → `valida=false` (`motivo=certificado_revocado`) if the certificate was revoked. The verdict is broken out: `firma_criptograficamente_valida` (cryptography+chain alone) and `revocacion_status ∈ {vigente, revocado, no_verificable, desconocido}`; `valida = cryptography AND revocacion_status=='vigente'`; a downed revocation channel → `valida=false`/`motivo=revocacion_no_verificable` but `firma_criptograficamente_valida=true` (fail-closed by default, `verify_personal_revocation_require`). Inc.1 `xades_personal` signatures (without `CertificateValues`) verify as XAdES-B/T (grandfathered) but **remain revocable** by certificate identity. **`nativa` signatures**: `payload_xml` is the original plaintext. Returns `{firma_id, valida, provider, xades_level?, nivel_conformidad?, acreditado, motivo?, sello_tiempo_valido?, tsa_timestamp?, lt_material_present?, archive_timestamp_present?, crl_next_update?, revocation_provenance?, ocsp_present?, ocsp_cert_status?, firma_criptograficamente_valida?, revocacion_status?}` (in `/verify` the **authoritative** field for the level actually verified is `xades_level`, derived from the result). A **public** endpoint (no auth). `404` if the signature does not exist.
- `GET /api/v1/signature/firmas/{firma_id}` — a signature's metadata (the same fields as `sign-xml`, without the full `certificado_pem`). Public. `404` if it does not exist.
- _(Internal, service-to-service; **not** reachable from the gateway — `X-Internal-Token` is not in `FORWARDED_REQUEST_HEADERS`)_ `POST /api/v1/signature/internal/sign-indice` `{payload_xml, objeto_id, version, objeto_tipo?, indice_id?}` — seals a XAdES-eligible object **attributed to the system principal `SYSTEM_RECONCILER_ID`** (not to a human), for archive-service's **reconciliation jobs** (the E15/E06 Inc.8 index, and the H-H transfer acta). Authenticated **only** by `X-Internal-Token` (fail-closed: empty setting → 503, missing header → 401, wrong header → 403); it requires neither `PERM_FIRMA` nor `X-User-Id`. `objeto_tipo` (default `"indice"`) is **validated against the allow-set `{"indice","acta_transferencia"}`** (`422` outside it) → only those two types produce the tenant's institutional seal (which does NOT derive from the actor); **structurally it cannot sign the personal chain**. The `firma.creada` entry carries `origen="reconciliation"`. The personal signature (`/requests/{id}/sign`, `/sign-xml`) NEVER accepts this token.

**Signature chain (the signer's inbox, RF-FIR-04).** All of these require `PERM_FIRMA`; the signer is derived from the JWT (never from the body, RF-FIR-11). The active turn is the lowest `orden` still pending. A personal signature requires **2FA** (RF-FIR-13): either an **email OTP** (E06 Inc.2) or, stronger, **TOTP RFC 6238** (device possession, E06 Inc.6, see [ADR-016 addendum Inc.6](adr/ADR-016-firma-electronica.md#addendum--incremento-6-2026-07-06-2fa-por-totp-rfc-6238-en-la-firma-personal)). The TOTP secret lives in **auth-service** (AESGCM-encrypted); see `POST /api/v1/auth/me/totp/*` above. **Personal signature format (E06/E17 F4, Inc.1)**: if the signer has an **active PKI signing key** (see `/me/signing-key/*`), the chain produces a per-user **XAdES-B/T** signature with their own certificate (third-party verifiable via `/verify`, `provider=xades_personal`); otherwise it degrades to the **native HMAC signature** (`provider=nativa`, backward compatible). Both are **art. 7 non-accredited** electronic signatures (`acreditado=false`) — the per-user PKI adds verifiability, format and cryptographic separation, not reinforced legal value (server custody ⇒ no sole control). **XAdES gate by level (Inc.3, `FIRMA_PERSONAL_REQUIRE_XADES` ∈ `off`|`clasificados`|`todos`, default `off`)**: with `clasificados`, an act at `nivel_seguridad>=2` **no longer degrades** to native — no active certificate → **`422 firma_xades_requerida`** (with enrollment guidance), auth-service down → **`503 canal_firma_personal_no_disponible`** (fail-closed); with `todos`, it applies at any level. A denial leaves an atomic `firma.xades_requerida_denegada` entry and **consumes** no 2FA factor. In a batch it is evaluated **per item**. The legacy boolean env var is accepted (`false`→`off`, `true`→`todos`); an unrecognised value aborts startup. Only enable `clasificados` after completing enrollment for signers with clearance>=2 (certificate issuance is offline).

- `POST /api/v1/signature/requests` `{objeto_tipo, objeto_id, titulo?, payload_sha256?, firmantes:[uuid,…], nivel_seguridad?}` — creates the chain (turns `orden` 1..N). `409 cadena_existente` if the object already has an open chain. _Note:_ to sign with 2FA the request must carry a stable `payload_sha256`.
  - **`objeto_tipo` is bounded** to `radicado` · `expediente` · `borrador` · `documento` (`422` outside the set). This is not cosmetic validation: the chain's security level used to be resolved by comparing this field —free text from the body— against the literal `"radicado"`, so `"Radicado"` was enough to bypass the authoritative source (RF-SEG-08).
  - **The level is decided by the object, not by the body.** If `objeto_id` resolves against `radicados` or `expedientes`, that row's real `nivel_seguridad` prevails and the body's is ignored — it is resolved **without looking at `objeto_tipo`**, so declaring `borrador` over a CLASSIFIED radicado's identifier lowers nothing either. `nivel_seguridad` only takes effect for unregistered objects (draft/document), where there is no table to consult.
  - **Fail-closed**: `objeto_tipo` ∈ {`radicado`, `expediente`} whose `objeto_id` does not resolve → `404 solicitud_not_found`, the same neutral answer as no-read-up (it does not distinguish "does not exist" from "you cannot reach it"). Previously it fell back to the level declared in the body.
- `GET /api/v1/signature/requests/{id}` — the chain's state: header + items `{orden, firmante_id, estado, firma_id, comentario, decidido_at}`.
- `GET /api/v1/signature/requests?objeto_tipo=&objeto_id=` — an object's open chain (`404` if there is none).
- `GET /api/v1/signature/pending?page=&size=` — the inbox: requests whose **active turn** belongs to the authenticated user (`X-Total-Count`).
- `POST /api/v1/signature/requests/{id}/challenge` — **(RF-FIR-13)** issues the 2FA challenge: it generates a 6-digit OTP bound to `(request, active-turn signer, document hash)`, emails it to the signer and returns `{challenge_id, factor_tipo:"otp_email", canal:"j***@dominio", expira_at}` (TTL 5 min). Only the active-turn signer (`403 no_es_su_turno`); `404 solicitud_no_encontrada` (neutral); **`422 payload_no_fijado`** if the request has no `payload_sha256`; `429 challenge_cooldown` if reissued within 30s; `503 canal_2fa_no_disponible` if the email cannot be sent (fail-closed). The OTP **never** appears in the response or in logs.
- `POST /api/v1/signature/requests/{id}/sign` `{payload_xml?, challenge_id?, otp?, totp_code?}` — signs the active turn (reusing the native HMAC signer) requiring **verified 2FA**; it advances the chain and, on signing the last turn, the request moves to `firmado`. **Factor selection**: if `totp_code` (6 digits) arrives and the signer has TOTP **active** → it is verified against auth-service (**no** prior `/challenge` needed); if `challenge_id`+`otp` arrive → email OTP; if `totp_code` arrives but the signer does **not** have active TOTP → **`422 totp_no_activo`** (it does not silently fall back to email); both → TOTP prevails; neither → **`409 2fa_requerido`**. TOTP errors: `401 totp_incorrecto` `{intentos_restantes}`, `423 totp_bloqueado`, `503 canal_2fa_no_disponible` if auth does not answer (fail-closed → rollback). Email-OTP errors: `401 otp_incorrecto` `{intentos_restantes}` (5 attempts → `challenge_bloqueado`), `409 challenge_expirado`/`challenge_consumido`/`challenge_documento_distinto`/`challenge_acto_distinto`, `403 challenge_ajeno`. Common: `403 no_es_su_turno`; `409` if the request is not pending or the turn race is lost (`FOR UPDATE` lock). 2FA is **non-disableable** for `nivel_seguridad ≥ 2` (RESERVED/CLASSIFIED) — both factors satisfy it; TOTP is the stronger one.
- `POST /api/v1/signature/requests/{id}/reject` `{motivo}` — rejects the active turn and **stops** the chain (request `rechazado`).
- `POST /api/v1/signature/batch` `{items:[{solicitud_id, challenge_id?, otp?, totp_code?}, …]}` — batch-signs the user's turns, with **one second factor per document** (RF-FIR-13): email OTP (`challenge_id`+`otp`) or `totp_code`; it does not abort on an individual failure (`{resultados:[{solicitud_id, resultado:'firmado'|'omitido', detalle?}]}`). _(Contract changed in E06 Inc.2: previously `{solicitud_ids:[…]}`.)_

### Postal shipments (E20) — document-service

The lifecycle of the physical dispatch of an outbound radicado. State machine `registrado → en_transito → entregado/devuelto/fallido` (`entregado`/`devuelto`/`fallido` are terminal). The human-facing routes require **`PERM_RADI_SALIDA`** and apply clearance-based no read-up.

- `POST /api/v1/documents/{document_id}/envios` `{operador, destinatario?, direccion?}` — dispatches the radicado with a carrier (auto-generated tracking number). `201`. It resolves the caller's clearance → `404 radicado_not_found` if the radicado does not exist **or** exceeds it (indistinguishable): what cannot be seen is not dispatched.
- `GET /api/v1/documents/{document_id}/envios` — the radicado's shipments (clearance-filtered).
- `GET /api/v1/envios?estado=&page=&size=` — tenant-wide listing (`X-Total-Count`, no read-up).
- `GET /api/v1/envios/{shipment_id}` — detail (`404` if the radicado exceeds the caller's clearance).
- `POST /api/v1/envios/{shipment_id}/estado` `{estado, descripcion?}` — a **manual** transition (human dispatch). It resolves the caller's clearance → `404` if the radicado exceeds it (closing D-14); `409 transicion_invalida` for a disallowed jump.
- `GET /api/v1/envios/{shipment_id}/tracking` — a **pull query by tracking number (F5)** against the carrier (`consultar_estado` stub); returns `{estado_interno_actual, estado_operador, estado_interno_mapeado, operador_conectado}`. Read-only, it does not apply the transition.
- `POST /api/v1/public/postal/callback/{tenant_slug}` `{operador, guia, estado_operador, event_id, descripcion?, motivo?}` — the carrier's **INBOUND webhook (F5, RF-POR-08)**. **No JWT** (it sits under `/api/v1/public/`); **HMAC-SHA256** authentication with the header `X-OrpycaMCP-Signature: sha256=<hex>` over the raw body, using the per-tenant/per-carrier secret from `postal_operator_credential` (the carrier's own identity, separate from the human permission — closing D-14 #3). The path's `tenant_slug` sets the `search_path`; the secret lives in that tenant's schema. **Idempotent** by `(operador, event_id)` (a retry is a no-op). It maps `estado_operador` onto the internal machine (`422 estado_operador_no_mapeable` if it does not map). Returns `{status: "applied"|"duplicate"|"recorded_no_change", shipment_id, estado}`. `401 firma_invalida` (no credential or bad signature, indistinguishable); `404 envio_no_encontrado` (unknown tracking number); `400 cuerpo_invalido`. A notification that would imply going backwards (or one over a terminal state) is recorded as `recorded_no_change` without mutating the state. Every transition emits a `document.envio.*` event for E16 and leaves an `audit_log` entry (`actor=postal:<operador>`).

### Interoperability — OAI-PMH (E11, RF-INT-02/INT-06) — document-service

> **OFF BY DEFAULT since the production audit.** These endpoints publish the correspondence register **anonymously** (number, subject, department, date) and, through CMIS `cmisselector=content`, the **bytes of the attachments**. Their only filter is `nivel_seguridad = 1`, which is the column's **default value**: the effective polarity was "public unless someone remembers to classify". Enable with `PUBLIC_INTEROP_ENABLED=true` in **both document-service AND archive-service** (same variable name in both on purpose, so a deployment cannot end up half-published). When off they return **404**, indistinguishable from a non-existent route. When on, every harvest and every download is audited (`document.oai_cosecha`, `document.cmis_consulta`, `document.cmis_descarga_anexo`, `archive.oai_cosecha`) with the verb, the arguments and the size delivered. Publishing the register is the entity's publication scheme (Ley 1712, art. 12), not a side effect of deploying the software.

- `GET /api/v1/public/oai/{tenant}?verb=...` — **OAI-PMH 2.0 metadata harvesting** of the tenant's **public** radicados. **No JWT** (under `/api/v1/public/`, like the postal webhook); the path's `tenant` sets the `search_path`. `application/xml` response. Verbs: `Identify`, `ListMetadataFormats`, `ListIdentifiers`, `ListRecords`, `GetRecord` (args `identifier`+`metadataPrefix`), `ListSets`. Selective harvesting via `from`/`until` (OAI dates) and `set` (= `doc_type` E/S/I). OAI identifier `oai:{tenant}:{tracking_number}`. Pagination by `resumptionToken` (100/page; the token preserves the `metadataPrefix`). Standard OAI errors (`badVerb`, `badArgument`, `cannotDisseminateFormat`, `idDoesNotExist`, `noRecordsMatch`, `badResumptionToken`).
  - **Two dissemination formats (RF-INT-06):**
    - `metadataPrefix=oai_dc` — simple **Dublin Core** (title=subject, identifier=radicado number, type, date, publisher, language).
    - `metadataPrefix=ead` — **EAD 2002 / ISAD(G)** (namespace `urn:isbn:1-931666-22-9`): each radicado is disseminated as a self-contained EAD fragment with `<archdesc level="item">` (a simple documentary unit) and the **6 ISAD(G)/NTC 4095 elements mandatory for exchange** — 3.1.1 `<unitid countrycode="CO" repositorycode>` (reference code: country+repository+number), 3.1.2 `<unittitle>` (subject), 3.1.3 `<unitdate normal>` (date), 3.1.4 `@level="item"`, 3.1.5 `<physdesc>` (medium), 3.2.1 `<origination>` (originating dependencia) — plus 3.4.1 `<accessrestrict>` (public, Law 1712/2014), 3.7.2 `<descrules>` (ISAD(G) 2nd ed. / NTC 4095), `<langmaterial>` and `<repository>`. **Declared debt** (roadmap): single-level `item` description; the multilevel fonds→section→series→file context (archive-service, TRD/CCD) is injected in a fast-follow.
  - **Access control (CRITICAL, Law 1712/2014 arts. 18-19, RF-SEG-08):** the trimming (`_PUBLIC` at the source) is **orthogonal to the format** — choosing `ead` neither dodges the filter nor creates an oracle for reserved records; a reserved/annulled/non-existent id → an indistinguishable `idDoesNotExist` in both formats.
- **Access control (CRITICAL, Law 1712/2014 arts. 18-19, RF-SEG-08):** harvesting exposes **only** what is **public** (`nivel_seguridad=1`, not annulled). The trimming is applied at the source, so the omission of reserved/classified records is **indistinguishable**: `completeListSize` counts only public records, `ListSets` does not enumerate a reserved-only _set_, and `GetRecord`/`ListMetadataFormats` over a reserved identifier return `idDoesNotExist` (they do not confirm its existence). There is no third-party credential with clearance in this scope (public harvesting).

### Interoperability — OAI-PMH for expedientes / multilevel EAD (E11, RF-INT-06) — archive-service

- `GET /api/v1/public/archive/oai/{tenant}?verb=...` — **OAI-PMH 2.0 metadata harvesting of the tenant's public EXPEDIENTES**, with **MULTILEVEL EAD 2002 / ISAD(G)** archival description. **No JWT** (under `/api/v1/public/`; the gateway routes `/api/v1/public/archive/` to archive-service **before** document-service's catch-all). `application/xml` response. Full OAI verbs; `set` = the expediente's `status` (open/closed/transferred). Identifier `oai:{tenant}:expediente:{code}`. Formats `oai_dc` and `ead`.
  - **Multilevel EAD (`metadataPrefix=ead`):** walking the `trd_series.parent_id` chain (the CCD, series↔subseries), each expediente is disseminated nested from the general to the specific — `<archdesc level="fonds">` (the institution) → `<dsc>` → `<c level="series">` → (`<c level="subseries">`…) → `<c level="file">` (the expediente) — satisfying ISAD(G) 2.2 (general→specific) and 2.4 (link to the higher level). The `file` level carries the 6 mandatory ISAD(G)/NTC 4095 elements (`<unitid countrycode="CO" repositorycode>`=code, `<unittitle>`=name, `<unitdate normal>`=opening→closing, `@level="file"`, `<physdesc>`=document count, `<origination>`=producing institution) + `<scopecontent>` (description), `<accessrestrict>` (public, Law 1712), `<descrules>` (ISAD(G)/NTC 4095). Without `trd_serie_id` → a flat `<archdesc level="file">` (an honest fallback).
  - It **complements** the **item-level** EAD of radicados (RF-INT-06 in document-service): document = radicados (simple documentary unit), archive = expedientes (compound documentary unit with its classification context).
  - **Access control (CRITICAL, Law 1712/2014 arts. 18-19, RF-SEG-08):** the chokepoint `_PUBLIC = "nivel_seguridad = 1"` on `expedientes`, applied at the source → a reserved expediente is **indistinguishable** (`idDoesNotExist`, public-only `completeListSize`/sets). **Item-level anti-leak boundary:** the description **stops at `level="file"`** — a public expediente may contain reserved radicados and archive does not hold their security level, so it **never enumerates the child `tracking_number`s** (only an aggregate count in `<physdesc>`). The series-chain CTE is bounded to depth 50 (an anti-DoS guard against cycles on the anonymous endpoint).

### Interoperability — CMIS 1.1 minimal read profile (E11, RF-INT-02) — document-service

Browser Binding (JSON). **Read-only** over the tenant's **public** radicados. **No JWT** (under `/api/v1/public/`); tenant by path.

- `GET /api/v1/public/cmis/{tenant}` — **repositoryInfo**: `{repositoryId: {repositoryName, cmisVersionSupported: "1.1", rootFolderId: "root", capabilities}}`. The `capabilities` reflect read-only (query/write/versioning at `none`/`false`).
- `GET /api/v1/public/cmis/{tenant}/root?cmisselector=children[&maxItems=&skipCount=]` — **getChildren** of the root: `{objects:[{object:{properties}}], numItems, hasMoreItems}` (public radicados as `cmis:document`).
- `GET /api/v1/public/cmis/{tenant}/root?cmisselector=object&objectId={numeroRadicado}` — **getObject**: CMIS properties (`cmis:objectId` = radicado number, `cmis:name` = subject, `cmis:baseTypeId`, dates in millis). `objectId=root` → the synthetic root folder.
- `GET /api/v1/public/cmis/{tenant}/root?cmisselector=content&objectId={numeroRadicado}` — **getContentStream**: streams the radicado's first attachment. An empty `objectId` → `400 invalidArgument`; an unsupported selector → `400 notSupported`.
- **Access control (CRITICAL, Law 1712/2014 arts. 18-19, RF-SEG-08):** the same trimming as OAI (`_PUBLIC` at the source). `getObject`/`getContentStream` over a reserved/annulled/non-existent id → an **indistinguishable** `404 objectNotFound`; `getChildren`/`numItems` count only public records. The content stream **never** serves the bytes of an attachment belonging to a non-public radicado (`get_public_anexo`, a public-only JOIN).

### Interoperability — Package export (E11, RF-INT-01) — document-service

- `POST /api/v1/export` `{comentario?, year?, doc_type?}` — **exports radicados to an interoperable package** (ZIP). **Authenticated** (not public); gate `USUA_PERM_EXPEDIENTE` + **per-radicado no read-up** (only what the caller can read is exported; those above clearance are omitted and reported content-free in the manifest). `application/zip` response with the headers `X-Export-UUID`, `X-Export-Count`, `X-Export-Excluded`. ZIP contents: `manifiesto.json` (export UUID, comment, start/end marks, list of radicados with SHA-256, exclusions), `esquema/radicado.schema.json` (the published **JSON Schema draft 2020-12**), `radicados/{numero}.json` (per-radicado data that **validates against the schema**: system + contextual metadata, disposition, classification, attachments, history), `anexos/{numero}/{i}-{archivo}` (binaries), `checksums.txt` (SHA-256 per member). The **radicado number is preserved** as identity (AGN Agreement 060/2001). Physical ZIP packaging is provisional (BagIt/OAIS+WORM = E10). Optional filters `year` (2000–2100) and `doc_type` (E/S/I).

- `POST /api/v1/import` — **imports an interoperable package** of radicados. **Authenticated**; gate `USUA_PERM_EXPEDIENTE` + **clearance-based no write-up**. It receives the INT-01 ZIP as a **raw body** (`Content-Type: application/zip`, cap 512 MiB). **ALL-OR-NOTHING validation before ingesting**: it validates every radicado against the service's trusted JSON Schema and **verifies the SHA-256 of every binary**; if anything fails → `422 paquete_invalido` **leaving no partial state**. It re-ingests **preserving the radicado number** (a collision is **skipped**, never overwritten); it rebuilds the E↔S relationship (`responde_a`) by number; it uploads the binaries to storage; it audits `radicado.import`. Anti zip-slip/zip-bomb guards. Response `{import_uuid, importados, omitidos_existentes, omitidos_clearance, relaciones_reconstruidas}`. A radicado whose `nivel_seguridad` exceeds the importer's clearance is **skipped** (`omitidos_clearance`).

### MCP layer (E18, ADR-019) — mcp-server, port 8009

It exposes the API as _tools_ to agents/LLMs, while itself being a client of the gateway. The **MCP protocol** is available over **stdio** (`python -m app.mcp_stdio`) and **streamable HTTP**.

**It is now routed through the gateway** (`/api/v1/mcp/` → mcp-server), which it was not before: catalogue and protocol were left outside, so an external MCP client had no way in except talking to the service behind the gateway's back, with no JWT validation and no `X-Tenant-Slug`. Exposing it is safe because the write gate no longer depends on the client (signed proposal, see above). The streamable transport is mounted at `POST /api/v1/mcp/stream` in addition to `/mcp`.

The session context is **two headers**: `Authorization` and `X-Tenant-Slug`. `X-User-Permissions` and `X-User-Roles` are **no longer read** — nobody produced the former and the gateway drops it as forgeable, so it always arrived empty and every permissioned tool was invisible over that transport; permissions come from `GET /api/v1/auth/me` with the caller's token, and with them the **CRUD level**. The equivalent environment settings (`MCP_USER_PERMISSIONS`, `MCP_USER_ROLES`) have been removed.

**Scope framework**: the `mi_alcance` tool and the `orfeo://alcance` resource return what the person can execute, what they can read, and what they **cannot** with its reason and its **alternative route**. It is always offered, even to someone who reaches no write at all.

Simple HTTP catalog API:

- `GET /api/v1/mcp/tools` — catalogue of the tools THIS caller can use, resolved against `/auth/me` (permission **and** level).
- `POST /api/v1/mcp/tools/{name}/invoke` `{...args}` — invokes the tool: propagates `Authorization` + `X-Tenant-Slug` to the gateway. Identity and permissions are resolved against `GET /api/v1/auth/me` using the caller's token (permission **and** CRUD level; `403` if insufficient). Tools: `radicar_documento`, `consultar_radicado`, `buscar_radicados`, `consultar_expediente`, `indice_expediente`, `listar_trd`, `bandeja_tramite`, `consultar_fuid`, `buscar_conocimiento` (E21 semantic search; POST `/api/v1/knowledge/search`, read-only, server-side ACL).
  **Write operations are NOT executed here: they return a proposal.** `X-Confirm-Write` is **no longer read** — it was a boolean any client could set, and it bound the confirmation to no particular act (it confirmed "something", not "this"). The response is `{kind:"proposal", ejecutado:false, proposal:{id, tool, operacion, campos, expira_en, token}, aviso}`: `campos` are the human-readable fields of the act — worded by the **catalog**, never by the model — and `token` is an opaque HMAC-SHA256-signed value carrying the tool and its already-validated arguments. It expires after 120 s and is bound to the user and tenant that requested it.
- `POST /api/v1/mcp/proposals/execute` `{token}` — executes the act signed inside the token. **The body carries only the token**: the arguments come from it, so what runs is exactly what was described. It **re-authorizes** on confirmation (the permission may have been revoked between proposing and confirming: a proposal attests _what_ was approved, not that whoever presents it may still do it) and **consumes** the proposal before dispatching. Errors: `400 propuesta_invalida`, `403 propuesta_ajena` / `permiso_insuficiente`, `409 propuesta_consumida`, `410 propuesta_expirada`.
  The same act over the **MCP protocol**: writes return the proposal as structured content and are confirmed with the `confirmar_accion(token)` tool; where the transport has a return channel (**stdio**) the user is asked in-band via **elicitation**, using those same fields. Over streamable HTTP (`stateless`) there is no session to ask and only the two-tool path remains — a declared limit. Tools carry MCP `annotations` (`readOnlyHint`, `destructiveHint`) as **hints**, so the client can add its own friction: the guarantee still lives in the signed proposal, not in them.
  _Declared limit_: the consumption registry lives in process memory — it covers double-clicks and retries, not multi-replica. `Idempotency-Key: <jti>` is emitted to the gateway as a hook for each epic to implement, not as a guarantee.

The current catalogue (45 tools, 16 of them writes). Every write returns a signed proposal and requires explicit confirmation (see above):

| Tool                        | Endpoint                              | Permission (crud)               |                                                   |
| --------------------------- | ------------------------------------- | ------------------------------- | ------------------------------------------------- |
| `radicar_documento`         | `POST /documents`                     | `PERM_RADI` (1)                 | **W** consumes an immutable number                |
| `tipificar_radicado`        | `PATCH /documents/{id}`               | `PERM_RADI` (**3**)             | **W** fixes the TRD series                        |
| `actualizar_radicado`       | `PATCH /documents/{id}`               | `PERM_RADI` (1)                 | **W** subject, destination, status, folios        |
| `consultar_radicado`        | `GET /documents/{id}`                 | _clearance_                     | R                                                 |
| `consultar_por_numero`      | `GET /documents/by-tracking/{tn}`     | _clearance_                     | R                                                 |
| `buscar_radicados`          | `GET /search`                         | _clearance_                     | R                                                 |
| `listar_anexos`             | `GET /documents/{id}/anexos`          | _clearance_                     | R                                                 |
| `consultar_disposicion`     | `GET /documents/{id}/disposition`     | `USUA_PERM_EXPEDIENTE` (1)      | R                                                 |
| `listar_tipos_documentales` | `GET /tipos-documentales`             | _none_                          | R                                                 |
| `crear_expediente`          | `POST /expedientes`                   | `USUA_PERM_EXPEDIENTE` (1)      | **W**                                             |
| `actualizar_expediente`     | `PATCH /expedientes/{id}`             | `USUA_PERM_EXPEDIENTE` (1)      | **W**                                             |
| `vincular_radicado`         | `POST /expedientes/{id}/radicados`    | `USUA_PERM_EXPEDIENTE` (1)      | **W**                                             |
| `cerrar_expediente`         | `POST /expedientes/{id}/close`        | `USUA_PERM_EXPEDIENTE` (**3**)  | **W** IRREVERSIBLE                                |
| `consultar_expediente`      | `GET /expedientes/{id}`               | `USUA_PERM_EXPEDIENTE` (1)      | R                                                 |
| `buscar_expedientes`        | `GET /expedientes/search`             | `USUA_PERM_EXPEDIENTE` (1)      | R                                                 |
| `consultar_foliado`         | `GET /expedientes/{id}/foliado`       | `USUA_PERM_EXPEDIENTE` (1)      | R                                                 |
| `hoja_ruta_expediente`      | `GET /expedientes/{id}/eventos`       | `USUA_PERM_EXPEDIENTE` (1)      | R                                                 |
| `indice_expediente`         | `GET /expedientes/{id}/indice`        | `USUA_PERM_EXPEDIENTE` (1)      | R                                                 |
| `listar_trd`                | `GET /trd`                            | _none_                          | R                                                 |
| `bandeja_tramite`           | `GET /workflows/inbox`                | _none_                          | R                                                 |
| `pendientes_equipo`         | `GET /workflows/pending`              | _sin permiso_                   | R                                                 |
| `tipos_transaccion`         | `GET /workflows/transaction-types`    | _sin permiso_                   | R                                                 |
| `hoja_ruta_radicado`        | `GET /workflows/{id}/history`         | _sin permiso_                   | R                                                 |
| `paso_actual`               | `GET /workflows/{id}/current`         | _sin permiso_                   | R                                                 |
| `asignar_radicado`          | `POST /workflows/assign`              | `PERM_TRAMITAR` (1)             | **W**                                             |
| `trasladar_radicado`        | `POST /workflows/transfer`            | `PERM_TRAMITAR` (1)             | **W**                                             |
| `completar_paso`            | `PATCH /workflows/{step_id}/complete` | `PERM_TRAMITAR` (1)             | **W** el id es del PASO                           |
| `ejecutar_transaccion`      | `POST /workflows/{id}/transactions`   | por tipo, suelo `PERM_TRAMITAR` | **W**                                             |
| `consultar_fuid`            | `GET /transferencias/{id}/fuid`       | `USUA_PERM_EXPEDIENTE` (1)      | R                                                 |
| `buscar_conocimiento`       | `POST /knowledge/search`              | _clearance_                     | R                                                 |
| `buscar_antecedentes`       | `POST /knowledge/antecedentes`        | _clearance_                     | R **query XOR radicado_ref**                      |
| `redactar_con_antecedentes` | `POST /knowledge/rag`                 | _clearance_                     | R returns text **with citations**, writes nothing |
| `listar_respuestas`         | `GET /documents/{id}/respuestas`      | _clearance_                     | R                                                 |
| `crear_respuesta_vinculada` | `POST /documents/{id}/respuesta`      | `PERM_RESPUESTA_VINCULADA` (1)  | **W** consumes an immutable number                |
| `listar_plantillas`         | `GET /plantillas`                     | `PERM_RADI` (1)                 | R                                                 |
| `listar_borradores`         | `GET /borradores`                     | `PERM_RADI` (1)                 | R                                                 |
| `crear_borrador`            | `POST /borradores`                    | `PERM_RADI` (1)                 | **W** no number yet; not an archival act          |
| `editar_borrador`           | `PATCH /borradores/{id}`              | `PERM_RADI` (1)                 | **W**                                             |
| `aprobar_borrador`          | `POST /borradores/{id}/aprobar`       | `PERM_RADI` (1)                 | **W** _not_ required before radicar               |
| `radicar_borrador`          | `POST /borradores/{id}/radicar`       | `PERM_RADI` (1)                 | **W** consumes an immutable number                |
| `consultar_serie`           | `GET /trd/{id}`                       | _no permission_                 | R                                                 |
| `consultar_retencion`       | `GET /trd/{id}/retention`             | _no permission_                 | R a computation, not the recorded disposition     |
| `ubicacion_expediente`      | `GET /expedientes/{id}/unidades`      | `USUA_PERM_EXPEDIENTE` (1)      | R where it is, not what it holds                  |
| `listar_prestamos`          | `GET /prestamos`                      | `USUA_PERM_EXPEDIENTE` (1)      | R                                                 |
| `reporte_radicacion`        | `GET /reports/radicados`              | _no permission_                 | R aggregates only                                 |

**Mutual exclusion declared, not discovered through a 422.** `buscar_antecedentes` takes either the subject (`query`) or the reference tracking number (`radicado_ref`), never both and never neither — enforced by `AntecedentesRequest` with a `model_validator`, and JSON Schema cannot express it with the catalogue's building blocks. The catalogue declares it via `exactly_one_of` and `coerce_args` answers `parametros_excluyentes` **before** the request leaves: the backend's 422 says the same thing, only after making the person wait.

**`aprobar_borrador` IS required** (fixed 2026-08-17). `POST /borradores/{id}/radicar` now answers `409 borrador_sin_aprobar` for an unapproved draft. Previously only the screen enforced it (`canRadicar = estado === "aprobado"`), so a direct `POST` skipped approval and consumed the number anyway — a control living only in the browser is not a control.

**Irreversible acts: a typed phrase, enforced by the backend.** A proposal whose tool declares `destructive` carries `irreversible: true` and `frase_requerida: "CONFIRMO"`, and executing it requires sending that phrase (`{token, frase}`). Who says the act is irreversible is **not the client**: it is the signed token, which the tool is read from. The UI disables the button as a courtesy; the barrier lives in `write_gate`, because a phrase validated only in the browser is theatre.

**The conversational assistant proposes too.** `POST /api/v1/assistant/message` returns `propuestas: []` (additive field) with the acts the model prepared. The `tool_result` fed back to the model explicitly says `ejecutado: false` so it cannot claim to have done something it did not, and the token never enters its context. Confirmation is `POST /api/v1/assistant/confirmar` with `{token, frase?}`.

**Left out by decision, not by oversight**: annulling and approving annulments, marking for destruction, setting the disposition, reclassifying the security level, publishing, unlinking a radicado (it breaks the foliation), transferring, repealing TRD/TVD, signing, and the whole tenant administration surface. **Bulk linking** (`/radicados/batch`, up to 500) stays out for as long as its confirmation card cannot be read: a card that says "500 radicados" does not let anyone confirm knowingly, which is the only reason the card exists.

**Two cross-service invariants** live in `shared/tests/`, the only CI lane that sees the whole repository: `test_rutas_mcp_alcanzables.py` (every catalogue path is in the gateway's routing table — the gap that already slipped through with bulk upload) and `test_permisos_declarados.py` (what the catalogue declares is what the endpoint requires, in both directions: under-declaring offers the tool to someone who will get a 403; over-declaring hides it from someone who can actually use it).

### Conversational assistant (E18, ADR-019/ADR-020) — via the gateway

Unlike the MCP catalog/protocol (which is **not** exposed through the gateway), the conversational assistant **is** routed by the `api-gateway` (`/api/v1/assistant/` → mcp-server), so it inherits its JWT validation and `X-Tenant-Slug` injection. The mcp-server orchestrates an **LLM loop (Claude)** that executes the catalog's **read-only** tools as calls to the gateway, **re-propagating the user's token** — the assistant runs _as-the-user_, never with credentials of its own, and every tool revalidates its RBAC/clearance in the backend.

- `POST /api/v1/assistant/message` `{mensaje: string(1..4000), conversation_id?: string}` — sends one user turn. Returns `{conversation_id, message: {id, role:"assistant", texto, formato:"markdown", ts}}`. The `conversation_id` (generated if omitted) keeps the thread; the history is **ephemeral per process** and **isolated per tenant** (a `conversation_id` never crosses tenants). **v1 = READ-ONLY**: the catalog's only write tool (`radicar_documento`) is **excluded** from the set offered to the model (defence in depth: filtered when building the prompt and denied again at dispatch). Content returned by the tools (e.g. inbound radicados drafted by third parties) is **data, never instruction** (an anti-injection boundary in the system prompt). `503 assistant_unavailable` if the assistant is not configured (no `ANTHROPIC_API_KEY`) or the SDK fails — **honest degradation**, never a simulated answer (RF-FIR-15); `401` without a token; `409 conversation_limit` when the thread's turn cap is exhausted. Tool `403`/`404` responses are communicated **generically** ("I could not find that record"), without revealing the existence of classified records (non-oracle, RF-SEG-08 / Law 1712/2014).

### Knowledge layer (E21, ADR-006) — knowledge-service, port 8011

An **advisory/derived and opt-in** layer (not a source of truth). Embeddings with a pluggable provider (local stub by default).

- `POST /api/v1/knowledge/ingest` `{source_type, source_ref, texto, metadata?, acl?}` — computes the embedding and persists the fragment (pgvector) with its ACL. To protect classified content, the ingester must include the level in `acl.nivel_seguridad` (1–3); absent = PUBLIC (1).
- `POST /api/v1/knowledge/search` `{query, top_k?, acl?}` — semantic retrieval (cosine kNN). **Access control resolved on the server (RF-SEG-08 / RF-BUS-04):** only fragments with `nivel_seguridad ≤` the caller's clearance are **ever** returned (derived from the JWT/DB, not from the request body); the client's optional `acl` only narrows further (e.g. by dependencia), it never widens access. Returns `[{id, source_type, source_ref, texto, metadata, score}]`. Tombstones (fragments purged by reclassification) are **excluded** from search.
- `POST /api/v1/knowledge/antecedentes` `{query|radicado_ref, source_type?, top_k?}` (XOR `query`/`radicado_ref`, E21 Inc.2, Pattern A) — pure ANCHORED and CITED retrieval, without an LLM: it reuses `search` (same ACL pre-filter) and returns `{antecedentes:[{source_type, source_ref, snippet, score}], advisory}` with a **fixed** notice (CC-01/CC-02). A non-existent `radicado_ref` **or** one exceeding the requester's clearance collapse to the same `404 antecedente_source_not_found` (anti-oracle, with no differentiated 403).
- `POST /api/v1/knowledge/rag` `{query, top_k?}` (E21 Inc.3, Pattern B) — generative RAG with citations. It retrieves with the same ACL pre-filter as `search` and, if `AI_PROVIDER` is a real generation provider (`ollama_local`, the sovereign default; `ollama_cloud`/`openai_compatible` external; `anthropic` with the native Citations API), it synthesises an ANCHORED answer. **Hard sovereignty barrier (no override)**: with an external provider it excludes from the context sent to the LLM any fragment at `nivel_seguridad ≥ RESERVED` — visible under ACL, but never egressed to a third party; if no generatable context remains, it falls back to pure retrieval (Pattern A) over everything retrieved. `AI_PROVIDER=disabled` (or unconfigured) also always falls back to Pattern A. Returns `{answer, citations:[{cited_text, source_type, source_ref, score}], proveedor, modelo, confianza, suggestion_id, advisory}` — `answer`/`modelo`/`suggestion_id` are `null` when there was no real generation. Every real generation is recorded in `kb_suggestions` (CC-03: `proveedor`/`modelo` in separate columns) and in `audit_log`. `503 rag_unavailable` on any provider failure (missing SDK, missing credential, network/API error) — honest degradation (RF-FIR-15), never a simulated answer.

> **Event-driven ingestion (E21, MVP):** besides the manual `POST /ingest`, the knowledge-service **consumes `orpycamcp.document.events`** (consumer group `knowledge-ingest` + DLQ, ADR-021) and auto-indexes every radicado on creation (`document.radicado.created` → `subject`/`sender`, `source_type="radicado"`, `source_ref=tracking_number`). The `nivel_seguridad` is **replicated from the event** (fail-closed to CLASSIFIED if absent) and **kept consistent under reclassification** (`document.radicado.reclassified`): an upgrade to RESERVED+ with an external embedding provider **purges the content** (tombstone), and a fragment's level **never goes down** except by an explicit reclassification, whatever order the events arrive in. RESERVED+ material is **not embedded** with an external provider (it is recorded in `audit_log`). An **annulled** radicado (`document.radicado.annulled`) is marked _voided_ **permanently** and stays excluded from search at any clearance (it is not re-indexed even if a creation event is reprocessed later). This is a **derived/advisory** layer (ADR-006): it is not a source of truth; the legal record lives in document/archive.

### Outbound webhooks (E11, ADR-018)

- `POST /api/v1/webhooks` `{url, event_types: [...], secret?}` — subscribes an external endpoint (empty `event_types` = all events). The `secret` is not returned. **Requires `USUA_PERM_ADMIN`** (the whole CRUD): registering a subscription delivers every tenant event to the given URL, so it is administrative configuration — without the gate it would be an exfiltration channel (and SSRF). **New `422`** if `url` points at an internal/non-routable destination (loopback `127.0.0.0/8`/`::1`/`localhost`, link-local `169.254.0.0/16`/`fe80::/10` — which covers the cloud metadata endpoint, private `10.0.0.0/8`/`172.16.0.0/12`/`192.168.0.0/16`/`fc00::/7`, an OrpycaMCP internal Docker network service name, or a scheme other than `http`/`https`): the `detail` explains the concrete reason for the rejection, not a generic "invalid URL". Strict by default; relaxable only in development with `WEBHOOK_ALLOW_PRIVATE_TARGETS=true` in notification-service (a kill switch, never in production).
- `GET /api/v1/webhooks` — lists the subscriptions (`X-Total-Count`). Requires `USUA_PERM_ADMIN`.
- `DELETE /api/v1/webhooks/{id}` — deletes a subscription. Requires `USUA_PERM_ADMIN`.
- When a domain event occurs, OrpycaMCP `POST`s the canonical envelope to the matching subscriptions, signed in the `X-OrpycaMCP-Signature: sha256=<hmac>` header (best-effort with retries). The receiver must be idempotent. Every delivery attempt **re-validates the destination with a real DNS resolution** (not only at subscription time): this closes the case of a domain that resolved to a public IP at subscription time and is later re-pointed at an internal IP (DNS rebinding), and it also covers subscriptions predating this hardening — if the destination is no longer valid, the delivery is skipped and logged, but the subscription is **not** deleted or deactivated automatically. Accepted limitation: a TOCTOU window remains between that revalidation and `httpx`'s actual connection (see `app/core/webhook_security.py`).

---

## Redis Streams Events

The workflow-service publishes to `orpycamcp.workflow.events` when the state of a flow changes:

```json
{
  "event_type": "flow_step_created",
  "radicado_id": "uuid",
  "tracking_number": "2024-ICETEX-E-000001",
  "to_dept": "Dirección Jurídica",
  "action": "assign",
  "tenant_slug": "icetex",
  "timestamp": "2024-01-15T10:30:00Z"
}
```

The notification-service consumes this stream (consumer group `notification-service`) and sends an email to the destination department.

---

## Batch API — document-service

> Allows creating multiple radicados or expedientes in a single call. Processing is asynchronous.
>
> **Routing contract (drift fix, 2026-08).** `document-service` and `archive-service`
> share the same root prefix `/api/v1/batch`, but each one owns a disjoint job type.
> Job status is therefore **nested under the job type**
> (`/batch/documents/{job_id}/status`, `/batch/expedientes/{job_id}/status`) — both
> services previously exposed the identical shape `GET /api/v1/batch/{job_id}/status`,
> which made it **impossible** for the api-gateway to decide which upstream to forward
> to by prefix (the `job_id` alone carries no service information). The `POST` already
> discriminated by subpath (`/documents` vs `/expedientes`); the `GET` now follows the
> same criterion. `POST /api/v1/batch/documents` and its status `GET` require
> `PERM_RADI` (same as the single-document `POST /api/v1/documents/`) — previously
> neither route required any permission.

### `POST /api/v1/batch/documents`

Creates a job to register 1-1000 radicados. Returns `202 Accepted` immediately. Requires `PERM_RADI`.

```json
// Request
{
  "documents": [
    { "doc_type": "E", "subject": "Solicitud 1", "sender_name": "Juan Pérez" },
    { "doc_type": "E", "subject": "Solicitud 2", "sender_name": "Ana Gómez" }
  ]
}

// Response 202
{ "job_id": "uuid", "job_type": "documents", "status": "pending", "total_items": 2, "created_at": "..." }
```

### `GET /api/v1/batch/documents/{job_id}/status`

Monitors the progress of the job. Requires `PERM_RADI`.

```json
// Response 200
{
  "job_id": "uuid",
  "job_type": "documents",
  "status": "completed", // pending | processing | completed | failed
  "total_items": 2,
  "processed_items": 2,
  "failed_items": 0,
  "items": [
    { "item_index": 0, "status": "success", "result_id": "uuid-radicado" },
    { "item_index": 1, "status": "failed", "error_message": "..." }
  ]
}
```

---

## Batch API — archive-service

### `POST /api/v1/batch/expedientes`

Creates 1-100 expedientes with linked radicados. Returns `202 Accepted`.

```json
// Request
{
  "expedientes": [
    {
      "name": "Pensión García 2024",
      "trd_serie_id": "uuid",
      "radicado_ids": ["uuid1", "uuid2"],
      "tracking_numbers": ["2024-ICETEX-E-000001", "2024-ICETEX-E-000002"]
    }
  ]
}
// Response 202 — same format as batch documents
```

### `GET /api/v1/batch/expedientes/{job_id}/status`

Monitors the progress of an expediente batch job. Same response shape as the document
batch status. Requires `USUA_PERM_EXPEDIENTE`.

---

## Full-Text Search & Reports — document-service

### Output templates and drafts (legacy parity)

- `GET/POST/DELETE /api/v1/plantillas` — reusable templates (`nombre`, `cuerpo`, `tipo_documental`).
- `POST /api/v1/borradores` `{doc_type, subject, dest_dept?, cuerpo?, plantilla_id?}` — creates a draft (if `plantilla_id` is given and no `cuerpo`, it inherits the body from the template).
- `GET /api/v1/borradores` (`?estado=`), `GET /{id}`, `PATCH /{id}` (edit only if not yet registered), `POST /{id}/aprobar` (draft→approved), `POST /{id}/radicar` (→ generates the real radicado and marks the draft `radicado`).

### Quick response (legacy parity)

- `POST /api/v1/documents/{id}/respuesta` `{subject?, dest_dept?, observations?, response_days?}` — creates a **Salida** (outbound) radicado in response to this one (inherits the `RE: …` subject and dependencia if omitted), linked to the antecedent (`responde_a`). Returns the new radicado.
- `GET /api/v1/documents/{id}/respuestas` — outbound documents that respond to this radicado.

### Two-step radicado annulment (legacy parity)

The law prohibits deleting radicados: they are annulled with supervisory approval.

- `POST /api/v1/documents/{id}/anulacion` `{causal, motivo?}` — requests (step 1). `409` if it is already annulled or there is a pending request.
- `POST /api/v1/documents/{id}/anulacion/aprobar` `{observacion?}` — approves (step 2): the radicado moves to `anulado` (the number is kept).
- `POST /api/v1/documents/{id}/anulacion/rechazar` `{observacion?}` — rejects. `GET /api/v1/documents/{id}/anulacion` — last request.
- `GET /api/v1/anulaciones?page&size` — **approver queue**: requests in state `solicitada`, oldest first (FIFO — a pending annulment blocks the document's lifecycle). Requires `PERM_PANU_CODI`, the **same** permission as deciding. Each row carries the request plus the radicado's `tracking_number`, `subject`, `doc_type`, `radicado_status`. `X-Total-Count` header. **No-read-up (RF-SEG-08) applied to rows AND to the count**: an unfiltered count would reveal how many requests exist over radicados the caller cannot see.

**Public single-window intake** (unauthenticated):

- `GET /api/v1/public/{tenant}/ventanilla/tipos` — document types the entity declares it accepts (`tipos_documentales.admite_ventanilla_publica`, **opt-in**). Does not expose the internal catalogue.
- `POST /api/v1/public/{tenant}/ventanilla` — external intake. The type must be declared admissible (**422 `tipo_no_admitido_en_ventanilla`** otherwise; never silently reclassified). Routed to the dependency flagged `depe_rad_tp2` ("web intake enabled"); if none is declared, **503 `ventanilla_no_habilitada`** — entering without a destination would leave the document in no one's inbox. The sender may be a person **or an organisation** (`sender_entity`). Identified senders must give a reply channel; **anonymous** intake is legitimate. Returns tracking number, verification code and destination dependency.
  **Multipart**: fields go in the `datos` part (JSON), attachments in `anexos` parts. One door for both the simple case and a writ with its evidence. Limits: **5 files, 10 MB each, 25 MB total**; PDF, JPG, PNG and TIFF only, **verified by content** — not by extension or `Content-Type`, which the client writes — and the stored MIME is the sniffed one. **No antivirus.** Attachments are stored BEFORE a tracking number is assigned: if storage fails, **503 `anexo_no_almacenado`** and nothing is filed, because a tracking number is immutable and must not exist without its evidence. The file is stored with no actor (`uploaded_by = NULL`).
- `POST /api/v1/public/{tenant}/pqrs` — **legacy**, kept because it is an already-published public surface; delegates to the single window and is not extended.

### Electronic signature (E06, ADR-016)

- `POST /api/v1/documents/{id}/signatures` `{ "content_hash": "<sha256 hex>", "anexo_id"?, "reason"? }` — signs the radicado/anexo: records identity (gateway claims), `content_hash` and timestamp. `422` if the hash is not SHA-256.
- `GET /api/v1/documents/{id}/signatures` — lists the signatures (`X-Total-Count`).
- `GET /api/v1/documents/{id}/signatures/verify?content_hash=<hex>` — `{content_hash, valid, signatures[]}`: `valid=true` if any signature of the radicado matches that hash (if the content changed, it stops verifying).

### Postal dispatches (E20)

- `POST /api/v1/documents/{id}/envios` `{operador, destinatario?, direccion?}` — dispatches the radicado via a postal operator (4-72/servientrega/otro); generates the tracking guide and `registrado` status.
- `GET /api/v1/documents/{id}/envios` · `GET /api/v1/envios/{id}` — dispatches of the radicado / detail with event history.
- `POST /api/v1/envios/{id}/estado` `{estado, descripcion?}` — updates the delivery status (operator callback/polling). State machine `registrado→en_transito→entregado|devuelto|fallido`; `409` if the transition is invalid.

**Mailbox inbox (E19).** The mailbox mirrored in the database, which is what lets several officers work the same account without colliding. Gate `PERM_RADI`; which accounts each person reaches is cut in the `WHERE`, as above.

- `POST /api/v1/email/cuentas/{id}/bandeja/sincronizar` `{password?}` — pulls from the filtering folder the messages not yet in the inbox. It lists identifiers first — which downloads nothing — and only fetches what is missing. Stable identity is the `Message-ID` (RFC 5322 §3.6.4), not the `uid`, which only identifies a message while the folder's `UIDVALIDITY` is unchanged (RFC 3501 §2.3.1.1); it is checked before fetching or moving, or a mailbox restore would register **another** message under this subject. The receipt date comes from the institution's server `INTERNALDATE`, not the `Date` header, which the sender's client sets and could backdate to gain a deadline.

- `GET /api/v1/email/cuentas/{id}/bandeja?estado=` — the messages, FIFO by receipt date. `X-Total-Count`.

- `GET /api/v1/email/cuentas/{id}/bandeja/estado` — pending, taken, and `ultima_novedad`, so the screen knows whether to reload without pulling the whole list.

- `POST /api/v1/email/cuentas/{id}/bandeja/{mensaje_id}/reservar` — a 15-minute reservation. This is what prevents two officers registering the same message twice: it is taken in the `WHERE`, so "zero rows" distinguishes "I took it" from "someone beat me to it" with no TOCTOU window.

- `POST /api/v1/email/cuentas/{id}/bandeja/{mensaje_id}/liberar` — releases your own reservation; it **does not touch anyone else's**, and it answers `204` whether or not there was something to release: an officer who opens a message, sees it is not theirs and closes it should not leave it locked for a quarter of an hour for the whole front desk.

- `POST /api/v1/email/cuentas/{id}/bandeja/{mensaje_id}/radicar` — creates the inbound radicado. Ordered by reversibility: binaries first (attachments and the original `.eml`, which Ley 527/1999 art. 12 requires to be kept in its own format with origin, destination, date and time), then the radicado, then the move to the "Radicados" folder, and last the acknowledgement to the sender if the mailbox has it enabled. The reservation is re-checked before closing.

- `POST /api/v1/email/cuentas/{id}/bandeja/{mensaje_id}/descartar` `{motivo}` — sets aside a message that reached the folder and should not be registered. Requires a reservation and a reason, and writes an `email.mensaje.descartado` entry into `audit_log` **in the same transaction**: it is not best-effort. Deciding that a received communication is **not** registered is as much a decision as registering it — it is the one that makes the document disappear from the archive — and until now it left only an `UPDATE` on an ordinary table, rewritable afterwards without a trace. The entry carries **neither** subject nor sender: a discarded message was never registered, so it has no classification level with which to cut its readability, and in `audit_log` every `USUA_PERM_ADMIN` would see it. The reason does travel, because it is the text of the decision, not of the document.

> **Legacy route retired.** `GET /api/v1/ingest/email/estado` and `POST /api/v1/ingest/email/poll` used to exist. They operated on a single mailbox declared in the environment through the `IMAP_*` variables and registered every unread message in one go. They are retired along with those variables: one mailbox per deployment does not express what Acuerdo AGN 060/2001 art. 13 requires, the password lived in clear text in the environment, and registering the whole inbox registered the advertising too. They are replaced by the account and inbox endpoints above, where the institution separates what gets registered from what does not.

### `POST /api/v1/public/{tenant}/pqrs` (citizen registration, Law 1755/2015)

**No authentication.** A citizen files a PQRS (citizen request: petition/complaint/claim): `{ "tipo": "peticion|queja|reclamo|sugerencia|denuncia", "nombre", "identificacion"?, "email"?, "asunto", "descripcion" }`. Creates an Entrada (inbound) radicado (`metadata.canal=pqrs_ciudadano`) and returns `{radicado_id, tracking_number, verification_code}` for later tracking with the verification endpoint.

### `GET /api/v1/public/{tenant}/verify/{code}` (public lookup, E13)

**No authentication.** Verifies the traceability of a radicado by its verification code (an unguessable token, distinct from the radicado number). The tenant travels in the path. Returns only NON-sensitive information: `tracking_number`, `doc_type`, `status`, `registered_at`, `dest_dept`. `404` if the code does not exist; `400` if the tenant slug is invalid.

### `GET /api/v1/reports/radicados`

Statistical summary of the tenant's radicados (E09): `total` and counts `by_doc_type`, `by_status`, `by_month` (`YYYY-MM`), `by_dependencia`. Optional filters `?date_from=&date_to=`. Native SQL aggregations (`GROUP BY`/`date_trunc`).

### `GET /api/v1/reports/radicados.csv`

Exports the summary in **CSV** (`seccion,clave,valor` with type/status/month/dependencia sections) — for periodic reports to the AGN/oversight bodies. Same filters `?date_from=&date_to=`.

```json
{
  "total": 8,
  "by_doc_type": { "E": 5, "S": 3 },
  "by_status": { "registered": 6, "archived": 2 },
  "by_month": [
    { "month": "2024-01", "count": 4 },
    { "month": "2024-02", "count": 4 }
  ]
}
```

### `GET /api/v1/reports/indice-reservado` · `.csv`

**Index of classified and reserved information** (E08, Law 1712/2014 art. 20 + Decree 1081/2015). A register of every radicado at `nivel_seguridad ≥ 2` with the metadata of the classification act. Gate **`PERM_RECLASIFICAR`**. It is **CONTENT-FREE** (it never exposes the subject or content) and **ORTHOGONAL to clearance** (the register is COMPLETE by legal mandate — it is a publication of metadata, not a reading of content). Mapping: `level 2 = reserved → art. 19`, `level 3 = classified → art. 18`. Each item: `{numero_radicado, tipo_radicado (E/S/I, NOT the TRD series), nivel_seguridad, clasificacion, excepcion_ley_1712, fundamento_juridico, causal_reserva, plazo_reserva_meses, fecha_clasificacion, fecha_radicacion}`. The metadata is populated when **registering something classified** or **reclassifying** (both require a `fundamento` when moving to level ≥2, `422` if missing — Law 1712 arts. 19/28) and is cleared on declassification. The `.csv` neutralises formula injection in the free-text fields. Generating it leaves an aggregate entry in `audit_log` (RF-BUS-10).

### `GET /api/v1/search`

Advanced radicado search (E09, ADR-014: PostgreSQL FTS). Full text with ranking (`tsvector`/`ts_rank`) + fuzzy (`pg_trgm`), combinable with structured filters.

Searches in: `tracking_number` (weight A), `subject` (B), `sender_name` / `sender_entity` (C), `dest_dept` / `observations` (D).

**Query params** (all optional): `q` (text, min 2 chars — **optional**: without text it is a filtered query ordered by date), `doc_type`, `status`, `date_from`/`date_to` (range over `registered_at`), `dest_dept_code`, `meta.<campo>=valor` (metadata filter, JSONB/GIN containment), `page`, `size`. Header `X-Total-Count`.

```bash
GET /api/v1/search?q=tutela&doc_type=E&date_from=2024-01-01&meta.area=juridica
```

```json
// Response 200
{
  "query": "tutela derechos",
  "total": 3,
  "page": 1,
  "size": 20,
  "hits": [
    {
      "id": "uuid",
      "tracking_number": "2024-ICETEX-E-000042",
      "doc_type": "E",
      "subject": "Acción de tutela derechos fundamentales",
      "sender_name": "María García",
      "status": "registered",
      "registered_at": "2024-06-01T10:30:00Z",
      "rank": 0.756
    }
  ]
}
```

---

## Workflow Rules Engine — workflow-service

> Configures automatic routing rules without code. When a radicado arrives, the active rules are evaluated in priority order.

Requires `X-Tenant-Slug` (and `X-User-Id` to record the author).

### `POST /api/v1/workflow/rules`

Create a rule. Two condition forms (compatible):

- **Operator engine** (`conditions` + `match_mode`): list of `{field, op, value}` combined by `all` (AND) or `any` (OR). Fields: `doc_type, doc_class, dest_dept, origin_dept, subject, sender_name, sender_entity, pages`. Operators: `eq, ne, contains, not_contains, in, gt, lt, gte, lte, regex`.
- **Legacy** (fixed columns `doc_type/doc_class/dest_dept/subject_contains`, AND): used if `conditions` is empty.

```json
// Request (operator engine)
{
  "name": "Tutelas voluminosas → Jurídica",
  "priority": 10,
  "match_mode": "all",
  "conditions": [
    { "field": "doc_type", "op": "in", "value": "E,S" },
    { "field": "subject", "op": "contains", "value": "tutela" },
    { "field": "pages", "op": "gt", "value": 10 }
  ],
  "assign_to_dept": "Dirección Jurídica",
  "assign_notes": "Responder en máximo 10 días hábiles"
}
// Response 201 — WorkflowRuleResponse with id, conditions, match_mode, timestamps
```

### `GET /api/v1/workflow/rules`

List rules. Query: `?active_only=true&page=1&size=50`.

### `GET /api/v1/workflow/rules/{id}`

Get rule by UUID.

### `PATCH /api/v1/workflow/rules/{id}`

Partial update (e.g.: change priority, enable/disable).

### `DELETE /api/v1/workflow/rules/{id}`

Delete rule. Responds `204`.

### `POST /api/v1/workflow/rules/evaluate`

Evaluate rules against a radicado (returns the first one that matches).

```json
// Request
{
  "radicado_id": "uuid",
  "tracking_number": "2024-ICETEX-E-000042",
  "doc_type": "E",
  "subject": "Acción de tutela por mora en servicio"
}

// Response 200 — if there is a match
{ "matched": true, "rule_id": "uuid", "rule_name": "Tutelas → Dirección Jurídica", "assign_to_dept": "Dirección Jurídica", "assign_notes": "..." }

// Response 200 — no match
{ "matched": false }
```

### `POST /api/v1/workflow/rules/evaluate/batch`

Evaluates several radicados (1-500) against the same set of rules in a single call.

```json
// Request
{ "items": [ { "radicado_id": "uuid", "tracking_number": "T1", "doc_type": "E" }, ... ] }
// Response 200
{ "results": [ { "matched": true, "radicado_id": "uuid", "assign_to_dept": "...", ... }, ... ] }
```

## Conversational assistant — the front↔gateway contract (E18/E22, ADR-020)

> **Correction of a stale claim (2026-08-07):** this section used to be titled
> "contract defined, backend PENDING" and declared the `mcp-server` a _scaffold_.
> That has been **false since E18**: the backend is implemented and documented
> above (see [Conversational assistant (E18, ADR-019/ADR-020) — via the gateway](#conversational-assistant-e18-adr-019adr-020--via-the-gateway)),
> with a real LLM loop executing the read-only tools _as-the-user_. This section
> is kept because it documents the other half of the contract — **what the
> frontend's SSR proxy does with the response** — which the backend section does
> not cover.
>
> The `503 assistant_unavailable` still exists, but it no longer means "the
> backend is not built": the mcp-server itself returns it when
> `ANTHROPIC_API_KEY` is missing or the SDK fails (honest degradation,
> RF-FIR-15). The SvelteKit proxy additionally translates any absent route
> (404/501/5xx/connection refused) into that same code — the UI shows "assistant
> unavailable", never a simulated answer.

### `POST /api/v1/assistant/message`

Sends one user turn and returns the assistant's reply. Authorization: **authenticated** (no fine-grained permission; every action the assistant triggers downstream is revalidated against the user's RBAC/clearance in the service that executes it). The identity headers (`X-Tenant-Slug`/`X-User-Id`/`X-Username`) are injected by the SSR proxy from `locals`, never from the body.

```json
// Request  (allow-list in the proxy: these two fields only)
{ "mensaje": "string (≤4000, trimmed)", "conversation_id": "uuid (optional)" }
// Response 200
{
  "conversation_id": "uuid",
  "message": { "id": "uuid", "role": "assistant", "texto": "reply (AUTHORITATIVE)", "formato": "markdown|text", "ts": "ISO-8601" }
}
```

- `texto` is the **source of truth**; `formato` is a rendering hint. The proxy converts `formato:"markdown"` to HTML with `marked` and **sanitises it server-side** (`sanitizeHtml`) before it crosses to the client — the assistant's output is **untrusted content** (XSS/prompt injection). The backend must **not** send HTML; if it did, the proxy ignores it.
- Reserved for the future (the current slice ignores them without breaking): `acciones[]`, `citations[]`, `usage`.
- Ready for **streaming** (not built): a `POST .../message/stream` (NDJSON/SSE) with the same `conversation_id` and a `message.id` stable per turn would allow appending deltas to the same turn without changing the shape.
- **Contract note (to reconcile)**: the proxy sends `X-Username` from the `preferred_username` claim (the frontend's `SessionUser` has no `username`).
