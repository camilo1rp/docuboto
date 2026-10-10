# API Reference

> **Scope note (2026-09-25).** Endpoint coverage is **identical** to the Spanish page and is
> verified mechanically — 338 endpoints matching in both directions:
>
> ```bash
> ep(){ grep -oE '`?(GET|POST|PATCH|PUT|DELETE) (/api/v1|/oai|/cmis)[^ `|]*' "$1" \
>       | sed 's/^`//' | sort -u; }
> diff <(ep docs/es/api.md) <(ep docs/en/api.md)
> ```
>
> What is **not** identical is prose density: a handful of endpoints are documented here as a
> single bullet where the Spanish page gives them a section with more rationale. No endpoint,
> parameter, status code or gate is missing; some of the *why* is shorter. Stated here rather
> than left for the reader to discover — the same rule this project applies to its changelog.

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

## Cross-cutting routes (all services)

- `GET /health` · `GET /health/ready` — liveness and readiness probes, in **all eleven** services. `/health` answers if the process is up; `/health/ready` also checks its dependencies (PostgreSQL pool, Redis, object store as applicable). auth-service's `/health/ready` adds `audit_particiones` (`ok`/`degraded`/`unknown`): `degraded` —and `"degraded": ["audit_particiones"]` at the root— if next month's `audit_log` partition is missing (the external `scripts/audit_partitions.py` cron stopped running); it also only reports and answers `200`. Docker Compose healthchecks and ordered startup rely on them: a service answering `/health` but not `/health/ready` is alive and **must not receive traffic yet**, and conflating the two produces the cascading startup that looks like an intermittent failure.
- `GET /api/v1/openapi.json` — the service's OpenAPI schema, served by FastAPI; the gateway aggregates them into `/docs`.

**Internal endpoints (`/internal/...` and the `-internal` suffix)**: sealed with `X-Internal-Token` (D-02) and **not routed by the gateway** — that header is not in `FORWARDED_REQUEST_HEADERS` — so they are unreachable from outside the internal network no matter how well the path is known.

| Internal endpoint | Service | Purpose |
|---|---|---|
| `POST /api/v1/internal/documentos/anexos` | document | FULL annex inventory for a batch of radicados (system act, no clearance narrowing) |
| `POST /api/v1/internal/documentos/metadata` | document | Metadata (`subject`/`doc_type`) of **visible** radicados for the export manifest — this one does narrow by clearance. Since 2026-10 it also returns **`tracking_number`** and **`doc_class`**: archive-service cross-checks them when linking a radicado (T6 / AREA-07-02, AREA-06-08) and fails closed without `tracking_number`, so document-service is deployed first |
| `POST /api/v1/internal/documentos/purgar` | document | **Destroys** radicados and their annexes under final disposition (RF-TRD-07 phase 5) |
| `GET /api/v1/internal/documentos/radicados/{ref}/huella` | document | SHA-256 of a radicado's **main document** (`ref` = number or UUID; ONLY the `es_principal` annex, the latest if there are several; **a supporting PDF does not count**: with no main one, `409 radicado_sin_documento`; the fingerprint storage computed on upload). `404 radicado_not_found` · `409 radicado_sin_documento`/`anexo_sin_huella` · `503 huella_no_verificable` (the 503 body does not forward what storage answered). Queried by signature-service when opening and signing a chain (AREA-04-09) |
| `GET /api/v1/internal/expedientes/{ref}/indice/huella` | archive | SHA-256 (`xml_sha256`, C14N) of an expediente's **current electronic index** (`ref` = code or UUID; latest version). `404 expediente_not_found` · `409 indice_no_disponible`. Own router with an `X-Internal-Token` gate (`INTERNAL_SERVICE_TOKEN`, = signature's `ARCHIVE_INTERNAL_TOKEN`); with no token configured, fail-closed 401 (AREA-04-09) |
| `POST /api/v1/storage/internal/artefacto` | storage | Uploads a SYSTEM-produced artifact (signed index, original `.eml`) under its own format allow-list |
| `POST /api/v1/storage/internal/anexo-externo` | storage | Annex received through the public single window |
| `POST /api/v1/storage/internal/purgar-huerfanos` | storage | **Safe deletion** of orphan binaries of a failed import: only what that import created and nothing in the tenant references (SHA-256 deduplication) and that **no `upload` reused through deduplication** (`files.dedup_hit_at`: anything shared is always kept; a missing references table fails closed); a result per file |
| `POST /api/v1/storage/internal/purgar-disposicion` | storage | **Physical deletion** from the object store + row, behind the WORM gate |
| `GET /api/v1/storage/files/{id}/content` | storage | Bytes for service-to-service processing (mediated download, AIP) |
| `POST /api/v1/preservacion/proteger-indice-internal` · `proteger-artefacto-internal` · `renovar-retencion-internal` | storage | WORM wiring and renewal from unattended reconciliation |
| `POST /api/v1/preservacion/aip-internal` | storage | AIP packaging driven by reconciliation (`SYSTEM_RECONCILER_ID`) |
| `POST /api/v1/preservacion/export-zip-internal` | storage | Expediente ZIP export requested by archive-service |
| `GET /internal/signing-key/revocation-status` · `POST /internal/signing-key/sign-digest` | auth | Revocation status and digest signing with the custodied key |
| `POST /api/v1/signature/internal/sign-indice` | signature | Deferred XAdES sealing of the index/record |
| `GET /api/v1/signature/internal/circuitos/{tipo}/{id}` | signature | State of the approval circuit (document-service queries it before approving a draft) |
| `POST /api/v1/signature/internal/circuitos/{tipo}/{id}/invalidar` | signature | Voids the live circuits of an object **whose text changed**; document-service calls it when a draft already submitted for signature is edited |
| `GET /api/v1/internal/documentos/borradores/{id}/huella` | document | Current digest of the body and of the document. **Content-free** (two digests, a version and a boolean), which is why it carries no clearance gate: there is nothing to narrow by security level |
| `POST /api/v1/render/documento-internal` | document-render | Generates the document in **PDF/A** from the already-merged, already-sanitised body |

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
  "roles": ["operator"],
  "expires_in": 287
}
```

`expires_in` is the seconds left on the token (`exp − now`, ≥ 0): the api-gateway caps its validation cache (Redis) TTL with it and does **not** cache when it is missing or ≤ 0. Cache entries are sealed with HMAC-SHA256 (`AUTH_CACHE_MAC_SECRET`, a gateway-exclusive secret); without the secret the cache is off. The token must be an *access token* (`typ=Bearer`) issued by a declared client (`azp` in `ALLOWED_AZP`, default `orpycamcp-frontend,api-gateway`) and carry `exp`, `iat`, `iss` and `sub`; an ID token or another client's token yields `valid:false`.

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
- `POST /api/v1/auth/users/{user_id}/totp/revoke` `{motivo}` — **administrative reset** (`USUA_PERM_ADMIN` level 3): removes **another** person's second factor so they can register it again themselves from `/perfil`, and voids their recovery codes in the same transaction. `200 {estado:"revocado", usuario_id, estado_previo, codigos_recuperacion_eliminados}`; `404 no_totp`; `409 restablecimiento_propio_no_permitido`; `422` if `motivo` is shorter than 8 characters. It **never** returns a secret nor enrolls on anyone's behalf — whoever could enroll for someone else would in effect hold their personal signature. The `totp.restablecido_por_admin` entry is **atomic with the removal** (same transaction: if the entry fails the factor is not removed; likewise `totp.desenrolado*` in self-service) and is attributed to the **administrator** (`actor`), with the affected user in `object_ref` and the `motivo` in the `payload`.
  - **Why it exists, and why it was not solved by accepting recovery codes when signing**: recovery codes restore the factor but **cannot sign** (`/internal/totp/verify` only classifies a TOTP), and `enroll`/`DELETE /me/totp` require the current code or a recovery code. Anyone who lost the phone **and** the codes was locked out, with no way out other than touching the database by hand. Letting a long-lived paper code sign would have downgraded the signing second factor in the very act that attests: the factor is restored, not degraded.
  - **It cannot be used on oneself** (`409`): an administrator who reset their own could remove it without proving device possession and enroll another right after — whoever stole their session would end up holding the second factor of their personal signature. **Two people** are always required; the administrator uses `/perfil` like everyone else.

**Recovery codes (E06, migration 015).** TOTP is a **possession** factor, and until this migration **every** exit from it — revoking it, re-enrolling it, and revoking the personal signing key — required a current TOTP code, i.e. required the very phone that had been lost: whoever lost it was **locked out**, with no way out other than manual intervention on the database. Now `activate` returns **10 single-use codes** (`recovery_codes`, `Cache-Control: no-store`) and any of them works as `step_up_recovery_code` in `enroll` (re-enrolling) and in `DELETE /me/totp` (revoking).

- They are stored **hashed** (scrypt with a per-code salt), not encrypted: unlike the TOTP secret, the server never needs them in plaintext, only to check them — so a dump of the table does not allow authenticating. They are shown **exactly once**; `GET /me/totp` returns `recovery_codes_restantes` (a count), never the codes.
- **Single use**, guaranteed by an `UPDATE ... WHERE used_at IS NULL`: two concurrent requests with the same code race for the row and only one wins.
- They are issued **on activation**, not on enrollment (until then the credential protects nothing) and in the **same transaction** as the activation — there is no instant with an active TOTP and no recovery path. Revoking or re-enrolling **deletes** the batch in the same transaction.
- **Deliberate boundary: they do not work for SIGNING.** `POST /internal/totp/verify`, which signature-service invokes when signing, does **not** accept them (its `code` is validated as a 6-digit TOTP). A recovery code is written on paper or held in a password manager: it is not possession of a device, and admitting it as the second factor of a signing act would weaken the attribution RF-FIR-13 exists for. Recovering access and signing are different acts and demand different proof.
- _(Internal, service-to-service inside the Docker network, **not** routed by the gateway)_ `GET /internal/totp/status` — (AREA-04-02) `{activo, user_id}`: does the bearer's owner have an **active** TOTP? Content-free; double gate `X-Internal-Token` + identity derived from the bearer; no provisioned user or no tenant → `activo=false` with no `user_id`. signature-service queries it to veto the email OTP on acts of level ≥ 2. `POST /internal/totp/verify` `{code, contexto?}` — called by signature-service when signing; **same double gate** as `/status`: `X-Internal-Token` (`SIGNING_KEY_INTERNAL_TOKEN`; missing ⇒ `401`, wrong ⇒ `403`, unconfigured ⇒ `503`) **plus** the user bearer, checked before the bearer is read (identity still comes from it) (a valid answer now carries **`user_id`**, the `auth_users.id` of whoever was verified, which signature-service cross-checks with the actor — AREA-04-05; `GET /internal/signing-key/status` carries the same `user_id`); it derives the identity from the **forwarded bearer token** (not from a body `user_id` → no IDOR), honours `locked_until` before comparing, and advances the `last_timestep` anti-replay counter in a single atomic `UPDATE`. Lockout after `TOTP_MAX_FAILED_ATTEMPTS` (default 5) for `TOTP_LOCKOUT_SECONDS` (default 900). Skew window ±`TOTP_SKEW_STEPS` (default 1).

**PERSONAL PKI signing key (E06/E17 F4, Inc.1 — server custody, `acreditado=false`).** Each user's private signing key lives **only in auth-service**, AESGCM-encrypted (KEK `SIGNING_KEY_ENCRYPTION_KEY`, **separate** from the TOTP one), and it NEVER leaves the service.

- `POST /api/v1/auth/me/signing-key/enroll` — generates the RSA-2048 pair in memory, encrypts the private key, issues a **CSR** (subject = the user's identity + `cargo`/job title on a best-effort basis from RBAC groups) and stays `pendiente_emision`; returns **only** `{csr_pem, estado}` — **never** the private key. Requires `PERM_FIRMA`. A re-enroll revokes the previously active key (step-up hardening deferred to Inc.2). Certificate issuance is **out-of-band** (`scripts/gen-user-cert.sh`, run with `ca.key` outside any container; the user sub-CA is distinct from the institutional seal's).
- `GET /api/v1/auth/me/signing-key` — `{estado, certificado_fingerprint?, not_after?}`. **Never** returns the private key.
- `DELETE /api/v1/auth/me/signing-key` `{step_up_totp_code, motivo?}` — **revokes one's own key**, requiring TOTP step-up (verify-and-consume; without a code → `422`) → `{estado:"revocado"}` (E06/E17 Inc.2). Writes a `signing_key.revocada` audit entry (with `revocado_at`/`motivo`) **atomically** with the UPDATE. `motivo` is a **closed code** (RFC 5280: `key_compromise`, `superseded`, `cessation_of_operation`, `affiliation_changed`, `privilege_withdrawn`, `unspecified`; `422` outside it; same on the admin endpoint). `404` if there is no key.
- `POST /api/v1/auth/admin/users/{user_id}/signing-key/revoke` `{motivo?}` — an **admin** (`USUA_PERM_ADMIN` level 3) revokes another user's key (the "departed employee" case): the target `user_id` travels in the path, the actor comes from the token (the two can never be confused). Atomic audit entry with `revocado_por`.
- _(Internal, **not** routed by the gateway)_ `GET /internal/signing-key/revocation-status?fingerprint=<hex>` — queried by signature-service when verifying a personal signature (`X-Internal-Token` + `X-Tenant-Slug`): it returns **only** `{status, revocado_at?}` (`status ∈ vigente|revocado|desconocido`) — never the certificate or the key. Tenant-isolated: a fingerprint from another tenant, or a non-existent one, returns `desconocido` (fail-closed, never `vigente`). The pending→active activation persists `user_ca_fingerprint`+`certificado_fingerprint` and validates `leaf.verify_directly_issued_by(sub_ca)` fail-closed.
- _(Internal, **not** routed by the gateway)_ `POST /internal/signing-key/sign-digest` `{digest_b64, alg:"RSA-SHA256", prehashed:true, totp_code}` — called by signature-service when signing a personal act in the chain: it derives the user from the **forwarded bearer token** (no IDOR), **verifies-and-consumes the TOTP atomically** with the signature, decrypts the key **in memory only** (minimum lifetime), signs the digest, and returns `{signature_value_b64, certificado_pem, certificado_fingerprint, serial, not_after}` — the private key is never returned. This is the "sign a digest" interface of an HSM (the seam towards qualified signature). `X-Internal-Token` is compared with `compare_digest`. The pending→active activation leaves an atomic `signing_key.activada` audit entry.

### RBAC user management (E14)

- `POST /api/v1/auth/users` · `GET /api/v1/auth/users` (paginated, `X-Total-Count`) · `GET /api/v1/auth/users/{id}/permissions` — user administration. The whole router requires `USUA_PERM_ADMIN` (reads level 1; `POST` level 3). `POST` writes an `rbac.usuario_creado` entry to `audit_log`, **atomically** with the INSERT (see the audit note below).
  - **`POST` with `is_root: true` → `403 forbidden` if the caller is not ROOT** ([ADR-024](adr/ADR-024-root-fuera-de-banda.md), closing a High self-escalation finding). A non-ROOT `USUA_PERM_ADMIN` can never create a ROOT user; ROOT can. A tenant's first ROOT is provisioned out-of-band (`app/ops/grant_root.py`, not over HTTP).
- `GET /api/v1/auth/users/pickable?size=N` — minimal projection `[{id, username}]` of **active** users, for UI pickers (e.g. the `UserPicker` used for approval chains, T-08c). Gated with `PERM_RADI` (not admin); it exposes neither `email` nor administrative metadata (D-10). `size` in `[1, 500]`.

### URD and dependencia context (E08)

- `GET /api/v1/auth/users/{user_id}/urd` · `POST /api/v1/auth/users/{user_id}/urd` · `DELETE /api/v1/auth/users/{user_id}/urd/{urd_id}` — manages the User-Role-Dependencia assignments (one per (user, dependencia); a single primary one). Requires `USUA_PERM_ADMIN` (`POST`/`DELETE` level 3). `POST`/`DELETE` write `urd.creada`/`urd.eliminada` entries, **atomically** with the mutation.
- `GET /api/v1/auth/context` — caller context: `{user_id, active_depe_id, available_depts[]}`.
- `POST /api/v1/auth/context/switch` `{ "depe_id": 120 }` — sets the active dependencia (organizational unit) for the user themselves. **Does not reissue the token** (ADR-013): it persists the context and audits it in `usua_historico` (Channel B, not `audit_log`). `400 no_urd_for_dependency` if the user has no active URD there.

### Groups, memberships and permissions (E08)

The whole router requires `USUA_PERM_ADMIN`: **reads** at level 1 and every **write** at **level 3** (`403` with the permission at read level: whoever receives it only to consult the audit log no longer administers RBAC).

- `POST /api/v1/auth/groups` · `GET /api/v1/auth/groups` — create and list groups. `POST` writes an `rbac.grupo_creado` entry.
- `PATCH /api/v1/auth/groups/{group_id}` `{name?, description?}` — updates only the fields sent (`400 empty_patch` with none, `409` on duplicate name, `404`); writes `rbac.grupo_actualizado` with `{campos, antes, despues}`. `DELETE /api/v1/auth/groups/{group_id}` — deletes an **empty** group: `409` if it has members or URDs (checked in the very statement that deletes, no window), `404` if missing, `204` with an `rbac.grupo_eliminado` entry (`{name, description}` of the deleted group). Both require `USUA_PERM_ADMIN` level 3.
- `GET /api/v1/auth/permissions` — permission catalog (`{id, nombre, descripcion}`). **`USUA_PERM_ROOT` is omitted from the catalog if the caller is not ROOT** ([ADR-024](adr/ADR-024-root-fuera-de-banda.md)) — an option the `PUT .../permissions/{id}` below would reject is not offered in the first place.
- `POST /api/v1/auth/groups/{group_id}/members/{user_id}` · `DELETE .../members/{user_id}` — add/remove a user from a group. They write `rbac.miembro_agregado`/`rbac.miembro_removido` entries. **No-write-up (RF-SEG-08):** adding a non-ROOT caller to a group whose clearance ceiling (`role_clearance`, 1 if none) exceeds their effective clearance gives `403 no_write_up`, with no entry; the gate lives in the `INSERT`'s `WHERE`. Already being a member is not an error.
- `GET /api/v1/auth/groups/{group_id}/members` (paginated, `X-Total-Count`) — the group's users. The read mirror of the add/remove above.
- `PUT /api/v1/auth/groups/{group_id}/permissions/{permission_id}` `{ "crud": 3 }` — sets the CRUD level (0-5) of a permission for the group. Writes an `rbac.permiso_grupo_asignado` entry (payload with `permission_id`/`crud`).
  - **Granting (`crud > 0`) `USUA_PERM_ROOT` → `403 forbidden` if the caller is not ROOT** ([ADR-024](adr/ADR-024-root-fuera-de-banda.md)). Revoking (`crud = 0`) is always allowed, even for a non-ROOT — only granting is blocked. This closes the self-escalation path through a group the actor is themselves a member of.
- `GET /api/v1/auth/groups/{group_id}/permissions` — permissions assigned to the group: `[{permission_id, nombre, descripcion, crud}]`. The read mirror of the `PUT` above.
- A `group_id` that does not exist **or belongs to another tenant** → an indistinguishable `404` (the `search_path` already scopes the tenant schema; never `403`, which would confirm the other tenant's group exists).

**Auditing of RBAC/URD writes (E08, closing a traceability gap):** every `POST`/`PUT`/`DELETE` in this section and in "URD" writes an entry to `public.audit_log` **within the same transaction** as the mutation (`async with conn.transaction()`) — not _best-effort_: if `audit.append` fails, the mutation is rolled back; if the audit library is not installed, the write responds `500 auditoria_no_disponible` **before** touching any row (the same fail-closed pattern as `signing-key`/`totp`). `tenant_slug` is internally mandatory (`X-Tenant-Slug`, already required by `get_tenant_conn`); the actor is the `X-User-Id` resolved by the gate.

### Security classification (E08, RF-SEG-08, Law 1712/2014)

- `GET /api/v1/auth/security-levels` — catalog of levels (1 Public, 2 Reserved, 3 Classified).
- `PUT /api/v1/auth/groups/{group_id}/clearance` `{ "max_level": 2 }` — sets the maximum accessible level for a group (requires `USUA_PERM_ADMIN` level 3). Writes a `clearance.grupo_actualizado` entry with `{group_id, previous_max_level, new_max_level}` (**atomic**, same fail-closed guarantees as RBAC/URD above) — the previous level is what lets an upgrade be told apart from a downgrade.
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
- **`object_ref` by code, UUID or index (AREA-03-07)**: an expediente is named in `audit_log` in three ways (the code in new entries; the expediente UUID and the index UUID in old ones, which are never rewritten). Filtering by the expediente **code** or a radicado **number** also returns the entries that name it by UUID, and the expediente's by its index (`expediente_indice`); the no-read-up trimming then applies to the same row, so widening the reference does not widen access. Every new entry declares its `canal` (`api`/`ui`/`mcp`; `system` in jobs, workers and command-line tools): the library no longer has a default value.
- At maximum clearance (3 = Classified, and ROOT) **no trimming is applied**: the audit log is served in full, which is what that clearance authorises.
- **Declared residue**: `id` is a monotonic sequence, so gaps between visible ids reveal _how many_ entries were hidden and, via neighbouring `ts`, _when_. `/audit/verify` is no longer a second channel for that figure (see below); the listing `id` stays raw: closing it needs an opaque per-entry identifier and migrating its consumers.

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

Recomputes the tenant's hash chain and reports its integrity (ADR-008). Requires `USUA_PERM_ADMIN`. The verification is **not** clearance-trimmed: the chain only has evidentiary value if recomputed whole, and over a subset it would always read as "broken". The response is content-free, so it reveals nothing about the object. **But the detail is for maximum clearance only (AREA-03-03):** `total` minus the trimmed listing's `X-Total-Count` was the exact number of hidden classified entries, and `id` is a global sequence across tenants. Callers below maximum clearance get only `ok` and `total_roturas`; `total`, `verificados` and `broken_id` are `null` and `roturas` is empty.

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

Registers an institution in `public.tenants` (`provisioning_status='pending'`) and writes a `tenant.created` entry to `public.audit_log` in the same transaction. It does **not** create the `tenant_{slug}` schema: the application role the service runs as cannot execute DDL; provisioning (schema, migrations, `GRANT USAGE`) belongs to `scripts/init_tenant.py`, which also activates the institution.

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

Update `name` or `status`. `slug` and `code` are immutable. Writes a `tenant.updated` entry (before/after) atomically with the change.

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

- **Dependencias** (organizational chart): `GET /api/v1/dependencias` (authenticated) · `POST/PATCH/DELETE /api/v1/dependencias` (**`USUA_PERM_ADMIN` at CRUD level 3**; level 1 or 2 gets `403`) · `GET /api/v1/dependencias/by-codigo/{codigo}` (hierarchical, authenticated).
- **Catálogos** (lookups): `GET /api/v1/catalogos` (authenticated) — **which catalogues exist**: returns the list of accepted names, so nobody has to guess them or keep a client-side copy. · `GET /api/v1/catalogos/{catalogo}` (authenticated) · `POST/PATCH/DELETE /api/v1/catalogos/{catalogo}` (**`USUA_PERM_ADMIN` at CRUD level 3**) — `tipos-identificacion`, `tipos-remitente`, `medios-recepcion`, `tipos-anexo`, `causales`, `formas-envio`, `soportes`, `mensajes-rapidos`.
  - **`causales` declares the act it serves** (`ambito`, tenant migration 008): `devolucion`, `anulacion`, `reasignacion`, `devolucion_postal` or `general`. `GET /api/v1/catalogos/causales?ambito=devolucion` filters at the source, and `ambito` travels in the `POST`/`PATCH` body. It is the only catalogue that supports it: asking for it on any other returns **422 `ambito_no_aplica`** instead of ignoring it silently. A causal with no declared ambit stays `general` and every act offers it.
- **Parameters**: `GET /api/v1/config/params`, `GET /api/v1/config/params/{key}` (authenticated) · `PUT /api/v1/config/params/{key}` (**`USUA_PERM_ADMIN` at level ≥ 3**: an administrator in read or edit mode gets `403`; the audit entry carries `antes`/`despues`). Body `{value, motivo?}`; `value: null` gives `422 valor_nulo` for any key (JSON `null` is only written by the seed). **`firma_personal_require_xades`** (`off`·`clasificados`·`todos`) is the entity's **XAdES policy**, read by signature-service on every chain signature; migration `010` seeds it as JSON `null` (= "the entity does not define it": the environment fallback `FIRMA_PERSONAL_REQUIRE_XADES` rules) and `011` repairs entities where an earlier `010` had seeded `"off"` and nobody had changed it through the API; any other value gives `422 valor_invalido` (an unknown value would be applied as `todos`). **Relaxing the policy** (lowering the requirement, e.g. `todos`→`clasificados`, or setting a lower value when the current one is `null`/unknown, which is assumed to be the strictest) **requires `motivo`** of at least 10 characters: without it, `422 motivo_requerido`; the `config.parametro_actualizado` entry carries `motivo` and `relaja_politica`. Tightening does not ask for it. The generic `/admin/parametros` screen already lists it: no dedicated screen. The numbering parameters (`formato_numero_radicado`, `depe_digits`, `consec_digits`, `expediente_number_format`) are **inert**: no service reads them yet.
- **Departments — `PATCH` and `DELETE /api/v1/dependencias/{id}`**: renaming, setting `activo=false` or deleting while live flow steps or workflow rules reference the name returns `409 dependencia_con_tramite_en_curso` with `pasos_vivos` and `reglas`.
- **Non-business days / holidays**: `GET /api/v1/config/holidays` (`?year=`, authenticated) · `POST /api/v1/config/holidays`, `DELETE /api/v1/config/holidays/{id}`, `POST /api/v1/config/holidays/seed` (`{year_from, year_to}` → seeds the automatically computed Colombian holidays) — all three writes require **`USUA_PERM_ADMIN` at level ≥ 3** (FIRMA-2: an administrator in read or edit mode gets `403`; the calendar sets the entity's legal deadlines); the seed leaves a `config.festivos_sembrados` entry in the same transaction.
- **Business days**: `GET /api/v1/business-days/calculate?from=&days=` (authenticated) — computes the due date applying weekends + holidays + the tenant's non-working days (basis for RF-RAD-04).

### PINAR — Institutional Archives Plan (E14, RF-ADM-08, AGN Agreement 003/2015)

A per-tenant archival planning instrument. Every write (plan lifecycle, critical
aspects, prioritization, objectives, projects, follow-up, instruments) requires
**`USUA_PERM_ADMIN` at CRUD level 3** (October 2026; level 1, read-only, used to suffice; reads stay at level 1) (`app/core/authz.py`, closing the same Critical finding as
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

> **The UI now consumes the whole module (2026-08-29).** Until then the screen used **6 of the 31 endpoints**: a plan could be created and walked through its lifecycle, but there was **no way to create an aspect, an objective or a project from the browser** — so the dashboard was always empty and the page itself called it "a later PINAR phase". A dashboard that can never be filled is not an empty state: it is a false green.

---

### Documentary elimination — batches (RF-TRD-07 **complete**, 2026-08-29)

`POST /api/v1/eliminacion/lotes` · `GET /api/v1/eliminacion/lotes` · `GET /api/v1/eliminacion/lotes/{id}`

**A resource of its own, not an appendix of `/trd`**: a batch is a procedure with a lifecycle `borrador → publicado → avalado → ejecutado`, or `anulado`. Proposing requires **`USUA_PERM_TRD` with `crud≥3`** and an accountable actor.

**None of this deletes anything.** Elimination happens by batch because the record of proceedings and the Committee's endorsement belong to the batch; this phase proposes and **freezes the inventory**, which is what gets published for third parties to object to and what the record will describe. It is not read live from the expediente — the expediente is going to cease to exist.

**Five admission conditions**, and rejections are **reported with their reason** rather than silently dropped: the due date must be **confirmed** by an authorized user; the disposition must **end in destruction** (`E`, `S`, `M` — Conservación Total never enters); the frozen instrument must **govern** it (ADR-025 D5); the **WORM lock must have expired** — the **maximum of the three** an expediente can carry (signed index, transfer record, AIP), because while one stands, COMPLIANCE mode lets nobody delete; and it must not already be in a live batch.

An expediente outside the proposer's clearance is reported as `no_encontrado`, **indistinguishable from a non-existent one**. When opening a batch the inventory is clearance-narrowed, but `total_items` comes from the database, not from counting what was returned.

**Phase 2 — prior publicity (2026-08-28).** `POST /api/v1/eliminacion/lotes/{id}/publicar` moves `borrador → publicado` and **freezes the inventory**. `publicado_hasta` is mandatory and future — **no default**: one does not invent how many days an institution gives for objecting to a document's destruction.

The inventory becomes visible **without authentication** at `GET /api/v1/public/archive/eliminacion/{tenant}/lotes` and `GET /api/v1/public/archive/eliminacion/{tenant}/lotes/{lote_id}`. **The chokepoint is the batch's STATE**, not a deployment switch: deliberately **not** tied to `public_interop_enabled`, because publishing the correspondence register is the institution's decision while publishing what it is about to destroy is a **duty of the procedure**. The chokepoint is written as the **mark of the act** (`publicado_at IS NOT NULL`), not as a list of states: **every batch that was ever published** stays public — `avalado`, `ejecutado` and `anulado` too. The record of what was eliminated cannot vanish upon eliminating it, and the record that something was **not** eliminated matters to whoever read that it would be. A `borrador` batch answers **404**.

**A non-existent slug and a non-existent batch answer the same (T6 / AREA-06-01-C).** `GET …/{tenant}/lotes/{lote_id}` first checks that the institution is hosted (`public.tenants`) and returns the same `404 lote_no_encontrado` as for an invented UUID; before, an unknown slug failed with `UndefinedTable` (500) and a real one gave 404, so anyone without a token could learn which institutions the installation hosts (the listing was already closed).

**A classified expediente's description is not published, but its row is.** The narrowing lives in the `SELECT`, not in the renderer. Omitting the row would fail the publicity duty precisely over what matters most to be able to object to; what is withheld is the **denomination**, where the content lives. It is flagged `nombre_reservado` so the page **says so** instead of leaving a mute gap.

Public page at `(public)/eliminacion`, linked from the portal. **The objection channel is not invented**: it is the institution's existing single window.

**Phase 3 — the Committee's endorsement and withdrawal (2026-08-29).** `POST /api/v1/eliminacion/lotes/{id}/avalar` (`USUA_PERM_TRD` **`crud≥4`**) moves `publicado → avalado` with three **mandatory** records — `acta_comite`, `fecha_aval` (not in the future) and `acto_administrativo` — enforced in the schema, the service and a migration-047 `CHECK`. The system does **not** decide: it records that a Committee did (AGN Agreement 001/2024 art. 4.3.2.5).

**No endorsement while the publicity period is still open.** The guard lives in the transition's own `WHERE` (`AND publicado_hasta < CURRENT_DATE`, server clock), not in a prior check that would leave a read-then-write window. The `409` **tells which of the three reasons** applies: `publicidad_en_curso` says until when, `lote_no_avalable` says what state the batch is in, and an unknown id gives **404**.

**Separation of duties (2026-10, T6 / AREA-06-01).** `avalar` and `ejecutar` require `crud≥4` — the level that enables the irreversible — and above all **whoever proposed the batch does not endorse it** (`409 separacion_de_funciones`): raising the level only narrows who *can*, and a single level-4 person would still do everything. The guard lives in the transition's `WHERE` (`AND creado_por <> $5`), not in a prior check. The attempt leaves the audit entry `archive.eliminacion_separacion_denegada`. `LoteEliminacionResponse` adds `avalado_por` so the UI knows whom not to offer `ejecutar`. **Atomicity**: publish, endorse, withdraw and mark-executed are one act together with their audit entry (`conn.transaction()`); before, the `UPDATE` committed on its own and an audit failure left the batch in its new state with no trace.

`POST /api/v1/eliminacion/lotes/{id}/anular` withdraws the batch with a `motivo` of at least 10 characters — allowed **up to the last moment, even once endorsed** (written by admission of the three live states, not by negating `ejecutado`), never after execution. A withdrawn batch that had been published **stays public**, with its reason.

Admin page at `/admin/eliminacion`.

**Phase 4 — the elimination record (2026-08-29).** `POST /api/v1/eliminacion/lotes/{id}/acta` · `GET /api/v1/eliminacion/lotes/{id}/acta` · `POST /api/v1/eliminacion/lotes/{id}/acta/firmar`.

**The record is what outlives what was eliminated.** Once phase 5 deletes the bytes it will be — together with the headstone and `audit_log` — the only evidence of what was destroyed, under which act and who authorised it. It is of **permanent preservation**: no retention schedule, no final disposition. It is produced **before** the deletion, because the inventory it describes comes from expedientes that are about to cease existing.

Two tables (migration `048`), same criterion as the transfer record: `eliminacion_acta` holds the **frozen content** and is *append-only by trigger*; `eliminacion_acta_firma` holds the **mutable** sealing state. Deliberate, verified consequence: once the record exists, the batch row **can no longer be deleted**.

The XML (`urn:orpycamcp:acta-eliminacion:v1`) is canonicalised with the inventory sorted by code — two builds from the same data must be byte-identical, or `acta_sha256` would mean nothing. That SHA is also anchored in the indelible `audit_log`.

**The record's inventory is complete, never clearance-narrowed** — a record missing the classified expedientes attests to nothing. What is narrowed is **who may read it**: the threshold is **derived from the content** (the highest security level in the inventory), and whoever falls short gets **403** with the reason, not a trimmed record — breaking the signature would be worse than refusing it.

`metodo_borrado` is mandatory with **no default** in any of the three layers: the record must state how documents are destroyed (AGN Agreement 001/2024 art. 4.3.2.5), and a system-invented sentence would be a claim nobody made about an irreversible act.

XAdES sealing runs post-commit, best-effort and bounded, with the transfer record's three outcomes and a manual retry. `acta_eliminacion` was added to signature-service's `XADES_ELIGIBLE_OBJETO_TIPOS`: **without it the record would silently fall back to the `nativa` HMAC**, marked "signed" while being structurally weaker. `acreditado` is always `false` (Law 527/1999 art. 7, never art. 28).

> **The record's WORM lock: closed on 2026-08-29.** The reason it was pending was one of **model**: the Object-Lock store assumed every protected artifact belongs to an expediente, and an elimination record covers **N** expedientes that are about to disappear. Migration `storage tenant/014` adds `objeto_tipo` (`expediente` | `lote`) with a `CHECK` that **enforces the pairing**, and the purge gate now requires `objeto_tipo = 'expediente'` explicitly — a row whose id belongs to a batch must never decide about an expediente's purge. The record also joins `CONTROL_INSTRUMENT_TIPOS`: like the transfer record it outlives the content and must not postpone its disposition. **Permanent preservation is materialised as 100 years**, the contract's maximum: Object-Lock demands a date, not an infinity.

**Phase 5 — secure deletion (2026-08-29). This destroys.** `POST /api/v1/eliminacion/lotes/{id}/ejecutar` (`USUA_PERM_TRD` **`crud≥4`**) · `GET /api/v1/eliminacion/lotes/{id}/lapidas`.

The only path in the system that deletes archival documents, and it **takes no body**: in a destruction there is nothing for the client to parametrise. What is destroyed is set by the inventory frozen at proposal time; under which act, by the endorsement.

**Six preconditions**, all checked **before** the first expediente is destroyed: batch `avalado`, **executor different from the endorser** (`409 separacion_de_funciones`), record present, record **signed**, record **under WORM** (`409 acta_sin_worm`, T6 / AREA-06-02) and the frozen inventory as the only source. Requiring the signature and the lock can block execution if signature-service or the object store is down — deliberately so: without the lock the signed object could be deleted from the store right after what it describes is destroyed. The record's WORM wiring is best-effort but **no longer single-attempt**: a failure schedules its retry with exponential backoff (`worm_reconcile_proximo_at`), the `run_once_actas_eliminacion_worm` sweep (same cycle as the transfer record's WORM) repeats it, once the threshold is exhausted `worm_reconcile_agotado` is set with a system audit entry, and the manual retry `POST …/acta/firmar` also completes the lock of an already-signed record.

**The chain**: archive-service → document-service (`/internal/documentos/purgar`) → storage-service (`/storage/internal/purgar-disposicion`, which finally gives a door to `purge_for_disposition`, written long ago with **no caller**) → `document.radicado.purged` → knowledge-service purges the vector. Within each leg, **bytes before rows**; across legs, **documents before the expediente** — otherwise the radicado links would cascade away and there would be no way left to know what had to be destroyed.

**All-or-nothing per expediente.** A failure leaves that expediente **intact and without a headstone**, reported in `fallidos` with its code; the batch does not become `ejecutado` while any remain. A headstone over live bytes would assert a destruction that never happened.

**The headstone** (`expediente_lapida`, migration `049`) is written **before** deleting the expediente, in the same transaction. It is **content-free**: code, series, extreme dates, volume and the authorising act — **not the denomination**, which is content.

> **Migration 049 released three foreign keys, and one of them averted a silent loss.** Two blocked the deletion; `transferencias` was `ON DELETE CASCADE` and would have taken the transfers and, in cascade, `transferencia_acta` — a control instrument storage-service's own code declares must outlive the content (Law 594/2000 art. 26). Single criterion: **what must survive keeps `expediente_id` without a foreign key**.

> **Why the vector purge uses the sentinel and not a `DELETE`**, even though the document is gone: under at-least-once delivery, a `created` redelivered after the `DELETE` would reinsert the chunk from scratch, unmarked, and the deleted document's text would be searchable again. The `DELETE` would erase the very marker that makes the purge irreversible.

> **Three phases remain**: Committee endorsement, the signed record, and secure deletion. `ejecutado` always travels as `false`.

---

## document-service — port 8003

> Requires the `X-Tenant-Slug` and `X-User-Id` headers (injected by the gateway).

### `POST /api/v1/documents`

Registers a new radicado (official tracking record). Assigns an atomic radicado number.

**Minimum data (T13 / AREA-09-02, AGN Agreement 060/2001 §5.2).** `doc_class` is mandatory for all three types; **Entrada and Interno** also require `dest_dept_code` and **Salida** requires `origin_dept_code`. Anything missing → `422` (`DocumentCreate` and `BatchDocumentCreate` share one rule, `app/core/datos_minimos.py`; no sequence number is consumed). Internal producers satisfy it as follows: the quick reply inherits origin (the Entrada's destination) and type from its antecedent; a radicado created from a draft derives the type from its template, the destination from the name in `dest_dept` and the origin from the issuer's department (see `POST /borradores/{id}/radicar`); the mailbox intake requires `doc_class` and `dest_dept_code` in the body (`422 datos_minimos_incompletos`).

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
- `metadata` (optional): variable metadata of the document type (ADR-007). If the `doc_class` has an **active template**, the values are validated against its JSON Schema before registration; if they do not comply → `422 metadata_validation_failed` and the document is not registered; the 422 envelope carries `field` (dotted path of the offending property, e.g. `ubicacion.ciudad` or `etiquetas.1`; absent when the failure is at the root) so the screen can mark the control (`POST /expedientes` carries it too). Without a template, they are stored as-is. The column is indexed with GIN.

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

**Current location (ADR-029).** `?ubicacion_dept_code=` (1-999999) filters by **where the radicado is now**: the dependencia where the workflow left it (distribute, return, roll back, reassign) or, if the workflow never moved it, the registration one. **It is not `dest_dept_code`**, which is who the document was addressed to and does not change on distribution. The filter lives in the same condition as the rows and the `COUNT` (next to the _clearance_): `X-Total-Count` never counts what the page cannot show. Each radicado carries `flujo_dept_code`/`flujo_dept` (what the workflow wrote; `null` if it never moved it), `ubicacion_dept_code`/`ubicacion_dept` (the one to read: `flujo_*` if present, otherwise the registration one) and `dest_dept`/`dest_dept_code` (the **registration** one, a legal snapshot that does not change). `ubicacion_dept_code` is `null` when the step's name does not match **exactly one** active dependencia (the name stays in `ubicacion_dept`). The holder (`flujo_usuario`) is **not** returned in listings.

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

**Per-attachment control and exclusions (T13 / AREA-07-12, migration `040`).** Publishing the radicado does **not** publish its supporting files: the body accepts `anexos_publicables: [anexos.id]` (none by default) and CMIS (`getContentStream`) only serves an attachment with `anexos.publicable = TRUE` of a published radicado (`oai_repository.get_public_anexo`). The act leaves the set in a known state (marks the listed ones and unmarks the rest; an attachment that belongs to another radicado → `422 anexo_no_pertenece`, rolling everything back); the response adds `anexos_publicados`, the audit entry carries `anexos_publicables`, withdrawing unmarks them all and classifying (level ≥ 2) does too. **Not publishable ex officio** (neutral `404`, no audit entry): anything that came in through the public window (`metadata.canal = 'ventanilla_publica'`) or is typed with a document type that `admite_ventanilla_publica` (third-party information, Law 1581) — read live, so it follows the entity's configuration. The migration keeps the disclosure of attachments of radicados that were **already** published.

### `GET /api/v1/assistant/alcance`

The caller's scope frame: **what they can do, what they can consult, and what they cannot — with their alternative route**. No permission of its own: it describes the asker's capabilities, resolved from their token.

It exists because permissions had been a silent filter: the catalogue hid what the user could not use, so the limit was discovered **by failing** — or by waiting for an action that was never going to happen. The frame states it up front and offers the way out when there is one ("you cannot send it, but you can transfer the radicado to correspondence").

It is **derived**, not maintained: from the tool catalogue — which declares permission, level, domain operation and alternative route — crossed with `/auth/me`, `/auth/clearance` and `/auth/context`. Adding a tool updates the frame by itself.

It includes `limites_declarados`: what the system does **not** distinguish, so as not to promise precision it lacks — the registration permission does not separate by document type (`PERM_RADI_ENTRADA`/`PERM_RADI_INTERNO` exist and **no gate uses them**), and reads are bounded by security level, not by dependencia.

It lives under `/api/v1/assistant/` because that is the only mcp-server prefix the gateway routes. It does not require the assistant to be available: knowing what you can do should not depend on a model being up.

### `POST /api/v1/expedientes/{id}/legal-hold`

Sets or lifts the **legal hold** (litigation freeze) on the expediente's WORM objects (AREA-08-07; Law 594/2000 art. 4, AGN Agreement 001/2024 4.3.2.6). Body `{activo: bool, motivo (>=10), acta_ref}` (extra fields give `422`) returns `{expediente_id, code, activo, objetos_afectados, objetos_sin_cambio}`. `USUA_PERM_ADMIN` with `crud>=3`. Motive and act are mandatory **both ways**. Missing expediente or one above the caller's clearance gives a neutral `404`; no protected WORM objects gives `404 sin_objetos_protegidos`; storage down or preservation off gives `503 storage_unavailable` (nothing is audited in archive). Own audit entry `archive.expediente_legal_hold_fijado|levantado` (with motive, act and counts) on top of storage's (see `legal-hold-internal`).

### `POST /api/v1/expedientes/{id}/publicacion`

**Proactive publication** act for the expediente (Ley 1712/2014 art. 12, archive migration `042`). Same contract as the radicado twin: `{publicar: bool, fundamento: str}` → `{code, publicable, publicado_at}`, `PERM_RECLASIFICAR` at `crud≥3`, justification mandatory in both directions, act and audit entry in a single transaction.

It closes the asymmetry the cross audit uncovered: the art. 12 correction had been applied to radicados and **not here**, so archive-service's `oai_repository._PUBLIC` was still `nivel_seguridad = 1` and the multi-level EAD OAI kept proactively disseminating every unclassified expediente — the column's default. The asymmetry was also implicit: anyone reading document-service's cut would assume this service did the same.

Two **deliberate** differences from radicados:

- The cut carries **no status condition**. An annulled radicado is withdrawn from circulation; a `closed`/`transferred` expediente is not — those are normal lifecycle states. Copying `status <> …` for symmetry would have silently unpublished the entire closed archive, which is most of a fonds.
- What is disseminated is aggregate: code, name, description, extreme dates and the **count** of radicados. Child `tracking_number`s are never enumerated (a public expediente may contain reserved radicados).

`publicable` and `publicado_at` are carried in `ExpedienteResponse`. **Compatibility**: existing expedientes become **non-publishable** after migration `042`.

**Screen**: the "Publicación" drawer in `/expedientes/{id}`. Over a reserved expediente it does not offer publishing — and says why — but it **does** offer withdrawal, which is the backend's real asymmetry.

### `GET /api/v1/documents/{id}`

Get by UUID. Includes the list of anexos (attachments). **Access control (RF-SEG-08):** if the radicado exceeds the caller's clearance, it answers `404` (it does not reveal its existence). **ADR-029:** it includes the current location (`flujo_dept_code`, `flujo_dept`, `ubicacion_dept_code`, `ubicacion_dept`) and, only here and in `by-tracking`, `flujo_usuario` (`auth_users.id` of whoever holds the step; `null` if the step belongs to an ownerless dependencia). None of this appears on the public surfaces (verification by code, OAI-PMH, CMIS).

### `GET /api/v1/documents/{id}/historial-acceso`

**Who read and who downloaded** this radicado (RF-BUS-10; Law 594/2000 art. 22). Paginated (`page`, `size`) with `X-Total-Count`.

Returns only `ts, service, actor, actor_nombre, canal, action, object_ref`. **Not** `payload` — it carries `nivel_seguridad` and the attachment's `file_id`, and it is not filtered on serialisation: **it is not read from the database at all**, which is the only way it cannot leak the day somebody adds a field to it — nor `id`/`prev_hash`/`hash`, which are the chain's mechanics and live under `/admin/auditoria`.

- **Accesses only**, by allowlist: `document.radicado_consultado` (opened the record) and `document.anexo_descargado` (downloaded a file). Creating, annulling or reclassifying are acts **on** the document, not reads **of** it; mixing them would turn this into a history of everything and bury what the reader came for. It is the same allowlist archive-service uses for the expediente's history — two lists answering "is this an access?" differently would produce two histories of the same fact.
- **No-read-up BEFORE touching `audit_log`**: a radicado above the caller's clearance answers `404 document_not_found`, the very body an unknown id gets. A 403 would be a confirmation: someone who cannot see the radicado would learn that it exists and how many people have looked at it.

### `GET /api/v1/documents/by-tracking/{tracking_number}`

Get by radicado number (e.g.: `2024-ICETEX-E-000001`). Same clearance scoping as the detail by UUID (`404` if it exceeds the caller's level).

### `PATCH /api/v1/documents/{id}`

Update `subject`, `dest_dept`, `observations`, `pages`, `doc_class` and `metadata`.
`tracking_number`, `doc_type`, `year`, `sequence` are immutable.
**Correcting `dest_dept` (ADR-029)** also rewrites `dest_dept_code` in the same `UPDATE`, resolved by name: the code of the **only** active dependencia with that name or, if there is not exactly one, `null` (changing only the name left another dependencia's code and the radicado showed up under `?ubicacion_dept_code=` where it no longer was). On a `closed` radicado `dest_dept` is historical data: `409 dest_dept_immutable`.
**`status` is no longer accepted (ADR-028): `422 status_managed_by_workflow`.** The radicado's status (`registered → distributed → in_progress → closed`) is moved by the processing workflow in the same transaction as the step —assign, transfer, return, roll back, reassign, mark as read, finish— and is not edited by hand. The field is not silently ignored: a client that sends it gets the error and the database is untouched. Status stays read-only in `GET /documents` and can still be filtered on (`?status=`).
**Typing (`doc_class`) requires `PERM_RADI` at `crud >= 3`; the rest of the PATCH, `crud >= 1`.** The document type fixes the TRD series, and with it the radicado's retention and final disposition (Ley 594/2000 art. 24): an act with that consequence cannot be exercised with read permission. The threshold is only checked when `doc_class` is present in the body, so anyone correcting a subject or a page count keeps the level that always sufficed.
**Re-typing re-validates the metadata.** The effective metadata — whatever comes in the body, or whatever the radicado already had — is validated against the **new** type's active template before anything is written; if it does not fit, `422 metadata_validation_failed` and the radicado is left untouched. A document cannot end up typed as X while carrying the variable fields of Y. `metadata` is **replaced wholesale**, not merged, and the template version used is rewritten whenever re-validation happens — including the `NULL` when the new type has no active template — so the radicado does not keep pointing at the previous type's schema. Sending only `metadata` re-validates too: otherwise the registration-time validation would be bypassable with a later PATCH.
**Access control (RF-SEG-08):** `404` if the radicado does not exist **or** exceeds the caller's clearance. The gate is essential because the response returns the full radicado (subject, sender, attachments): without it, the write would also be a read.
**`409 radicado_anulado`:** an annulled radicado cannot be modified. Until ADR-028 `status` accepted `registered`, so without this guard a PATCH would undo an annulment approved through the two-step circuit, with no grounds and no approver. Un-annulling, if ever warranted, is an act of its own — not a side effect of editing a field. It is a `409` and not a `404` because at that point the radicado already passed the caller's clearance gate: saying it is annulled reveals nothing the caller could not already read.
**Audit:** writes a `document.radicado_actualizado` entry with the **before and after** of every field touched, in the SAME transaction as the mutation — if the entry fails, the change is rolled back.

### `PATCH /api/v1/documents/{id}/security-level`

**Reclassification of the security level** of an already-registered radicado (E08, parity with Orfeo's _Cambio Nivel de Seguridad_). Changes `nivel_seguridad` (1=PUBLIC, 2=RESERVED, 3=CLASSIFIED) without altering content or `tracking_number` (immutability intact). Requires the **`PERM_RECLASIFICAR`** permission.

- **Body**: `nivel_seguridad` (1..3, mandatory), `motivo` (non-empty string, **mandatory** — an act of reservation must be reasoned, Law 1712 arts. 19/28), plus optional `causal_reserva`, `fundamento_juridico`, `plazo_reserva_meses`.
- **Access control (RF-SEG-08)**: _no read-up_ — if the caller's clearance is below the radicado's current level it answers a neutral `404` (revealing neither existence nor level); _no write-up_ — it cannot set a level higher than the caller's own clearance (`403`). If the requested level equals the current one, it is an idempotent no-op (no audit entry, no event).
- **Trace**: records `nivel_anterior`, `nivel_nuevo`, `motivo` and the legal basis in the immutable `audit_log` (`document.radicado_reclassified`), atomically with the change.
- **Transactional outbox (T13 / AREA-09-05, migration `042`)**: the event is NO longer best-effort. It is inserted into `eventos_salientes` (per tenant) in the **same transaction** as the level `UPDATE` and the audit entry: if any of them fails, none of the three exists. After the commit it is published on the fast path and `enviado_at` is set; if Redis is down right then, a **sweep** relay (`barrido.py`, phase 4, `SKIP LOCKED`, after a 60 s grace) re-sends it with the **same** `event_id`/`dedup_key` (at-least-once; consumers already deduplicate). A re-sent `reclassified` carries the **current** `nivel_nuevo` from `radicados.nivel_seguridad`, not the one it had when queued, so a late re-send cannot lower a level another reclassification already raised. The same mechanism covers `document.radicado.created` and `document.radicado.annulled`, and emits `documento.contenido_cambiado` (E23, AREA-09-16) when `PATCH /borradores/{id}` changes the **text fingerprint** (not when only the subject changes): payload `{borrador_id, radicado_padre_id, cuerpo_sha256, cuerpo_sha256_anterior, version}`; it is inbox housekeeping — the synchronous guarantee remains signature-service's `409 documento_modificado`. The double control for declassification and a reconciliation sweep in knowledge remain pending.
- **Propagation**: emits `document.radicado.reclassified`; signature-service re-synchronises the `nivel_seguridad` snapshot of the object's signature chains (closing the RF-SEG-08 staleness residue).

---

## storage-service — port 8005

> Requires the `X-Tenant-Slug` header. Files stored in the object-store bucket `orpycamcp-{slug}-documents`.

### `POST /api/v1/storage/upload`

Uploads a file (`multipart/form-data`, field `file`).

- Computes SHA-256; **deduplication is for the SAME author only** (AREA-08-09): if the uploader already uploaded that content, their record comes back with `200`. If the content exists as **someone else's** annex (or an anonymous/system one), a new row is created with **the caller's** `filename` and author and the answer is `201`: it used to answer `200`, which confirmed that a guessed content existed as a reserved annex and returned its original `filename` and `id`. With no identified author (public window, system artefacts) there is never deduplication
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

### `GET /api/v1/storage/files/{file_id}/content`

_(Internal, service-to-service; **not** reachable from the gateway — `X-Internal-Token`.)_ Returns the file's byte stream so another service can process it without going through the browser (document-service uses it when mediating an attachment download, and so does AIP packaging). This is the route that makes the download **mediation** possible: storage stays sealed and no-read-up is decided by whoever knows the expediente.

### `GET /api/v1/storage/files/{file_id}/download`

Returns an object-store pre-signed URL valid for 1 hour.

```json
{
  "url": "http://minio:9000/orpycamcp-icetex-documents/uuid/solicitud.pdf?X-Amz-...",
  "expires_in": 3600
}
```

### `GET /api/v1/storage/files/{file_id}/verify`

Re-verifies integrity (RF-DIG-02): downloads the object from the object store, recomputes the SHA-256 and compares it with the recorded one.

```json
{
  "file_id": "uuid",
  "expected_sha256": "...",
  "actual_sha256": "...",
  "integrity_ok": true
}
```

- `409 integrity_check_failed` if the object in the object store does not match the recorded fingerprint (integrity alert).

### `POST /api/v1/storage/files/{file_id}/replace`

Replaces an anexo (attachment) by creating a **new immutable version** (RF-DIG-01; `multipart/form-data`, field `file`). The previous version is kept.

- `201` with the new version (`version` incremented); `200` if the content is identical (no version created); `409 not_current_version` if `{file_id}` is not the current version; `415` if the format is not allowed.

### `GET /api/v1/storage/files/{file_id}/versions`

Returns the version chain of the anexo (ordered by `version`). Each element includes `version` and `is_current`.

### `DELETE /api/v1/storage/files/{file_id}`

Deletes from the object store and from the database. Responds `204`.

### Digital preservation (E10, OAIS/AGN 001/2024)

- `POST /api/v1/storage/internal/files/procedencia` `{file_id}` — **internal** (`X-Internal-Token`; B2, ADR-031): `{sha256, uploaded_by, referencias}` of a live file (`404 file_not_found` if absent or deleted). `referencias` are the sources (`table.column`) using it today (attachments, email, draft, shipment receipt, WORM…; a missing table counts as `table(ausente)` = referenced, fail-closed); `uploaded_by` may be `null` (anonymous or system upload). document-service uses it to check a shipment's receipt.
- `POST /api/v1/storage/internal/files/hashes` — **internal** (`X-Internal-Token`): SHA-256 for a batch of files. Consumed by `archive-service` when linking a radicado, to populate each document's `valor_huella` in the electronic index (E15 T-08, RT-15 art. 4.3.2.3). An id missing from the response means "there is no hash to give" (deleted or non-existent): the caller declares it as *no hash*, it does not invent one.

- `GET /api/v1/preservacion/estado` → `{enabled: bool}` — **instance state** (`PRESERVATION_ENABLED`), not per expediente: it reads nothing per expediente, so it opens no no-read-up oracle. Read gate `USUA_PERM_EXPEDIENTE` (same as the AIP mediator). The expediente's Preservation tab queries it before asking for the AIP and, with `enabled=false`, says "disabled on this installation" instead of the neutral 404 ("no AIP or no visibility"), which with preservation off made it look normal that nothing is ever packaged. Admitted per sub-route at the gateway.
- `GET /api/v1/preservacion/plan` (current) · `PUT /api/v1/preservacion/plan` (new version) · `GET /api/v1/preservacion/plan/versions` — Versioned Digital Preservation Plan: `formatos_destino`, `formatos_origen`, `num_copias`, `periodicidad_fixity_dias`, `politica_migracion`, `contingencia`. `formatos_origen` (MIME types) is the **institution's obsolescence policy**, consumed by the format-migration sweep (RF-PRE-04): the `type/subtype` shape is validated and the values normalised to lowercase without duplicates — a bare `"msword"` would match no file at all, so the policy would read as declared while converting nothing; it is rejected with `422` instead of stored.
- `POST /api/v1/preservacion/eventos` `{object_ref, tipo, resultado, hash_before?, hash_after?, detalle?}` · `GET /api/v1/preservacion/eventos?object_ref=` (**repeatable**: `?object_ref=a&object_ref=b`, up to 200 per request; AREA-07-15) — immutable PREMIS log. _(Internal, sealed with `X-Internal-Token`, not routed by the gateway)_ `POST /api/v1/preservacion/eventos-internal` `{object_ref, tipo, resultado, detalle?, agente?, dedup_key?}` records an event produced by **another service** with no person behind the HTTP request (archive: the fixity check of a transfer's receipt, AREA-07-05); it is **idempotent by `dedup_key`** (`201` the first time, `200` with the existing one afterwards: reconciliation retries over an append-only log). `tipo` ∈ INGESTA/FIJACION/MIGRACION/VALIDACION*PDFA/WORM/REPLICA; `resultado` ∈ `ok`/`fallo`/**`no_evaluado`** (a third state since E10 C2a, migration `008`: PDF/A validation with the default stub honestly reports `no_evaluado` — *"not evaluated" ≠ "passed"\_). When packaging the AIP, every **PDF** document (detected by the `%PDF` magic bytes) emits a `VALIDACION_PDFA` (**advisory** — a non-PDF/A PDF does not block the AIP) plus a PREMIS `validation` event. Config: `PRESERVATION_PDFA_VALIDATION_ENABLED` (default `false`), `PDFA_VALIDATOR` (`stub`|`verapdf`, default `stub`), `PDFA_PROFILE` (default `2b`). **The real veraPDF validator (subprocess) is implemented (E10 C2b)**: with `PDFA_VALIDATOR=verapdf` + `VERAPDF_BINARY` (absolute path to the binary, present in the **deploy** image — the dev image degrades honestly to the `no_evaluado` stub), `SubprocessVeraPdfValidator` runs veraPDF under a hardened security model (scrubbed ENV, guaranteed `killpg`+reap, semaphore, 0700 tempdir, timeout→`no_evaluado`, JSON-only). Tuning: `VERAPDF_TIMEOUT_SECONDS` (60), `VERAPDF_MAX_CONCURRENCY` (1), `VERAPDF_JVM_MAX_HEAP_MB` (512), `VERAPDF_JVM_MAX_METASPACE_MB` (256).
- `POST /api/v1/preservacion/aip` `{expediente_id, documentos:[{document_id, file_id, nombre}], indice_file_id?, retain_until? | (retencion_anios + fecha_inicio)}` — **(E10 Increment B, [ADR-023](adr/ADR-023-worm-object-lock-preservacion.md))** packages the **real OAIS AIP**: for each document it resolves the object by `file_id`, downloads the bytes from the object store, **recomputes the fixity** (SHA-256 vs `files.sha256`; mismatch → `409` + a failed `FIJACION` event), assembles a **BagIt bag (RFC 8493)** (`data/` with the bytes + `bagit.txt` + `bag-info.txt` with a real Payload-Oxum + `manifest-sha256.txt` + `premis.xml` [PREMIS v3, official namespace, validated against a local XSD profile] + `indice-firmado.xml` [if `indice_file_id`] + `tagmanifest-sha256.txt`), **serialises it to ZIP**, uploads it to the preservation **WORM bucket** with `Retention(COMPLIANCE, retain_until)` derived from the TRD , and records it in `preservacion_worm_objeto` (`tipo='aip'` → covered by the disposition gate). **Only `X-Internal-Token` (D-02, AREA-08-16)**: not routed by the gateway; archive-service calls it. `nombre` is validated (no `/`, `..`, control chars) and must be **unique** within `documentos` (`422`). `201` with `{id, expediente_id, aip_sha256 (of the ZIP), num_documentos, estado, bucket, object_key, aip_bytes, formato, retain_until, retention_mode, legal_hold, premis_xml, replicado, replica_target, replica_copias_hechas, created_at}`; `400` invalid retention; `404/422` non-existent `file_id` or duplicate `nombre`; `409` fixity mismatch or `worm_singleton_conflict` (that `(expediente, tipo)` is already protected with a **different** `file_id`; nothing is uploaded); `422` if the body carries `legal_hold`; `503` if `PRESERVATION_ENABLED=false`. **REPLICA (C1b)**: if `PRESERVATION_REPLICA_ENABLED=true` and the plan has `num_copias≥2`, after uploading the primary AIP an **immutable replica** is created in a second WORM bucket (`replica_target="local"` = a second bucket in the same object store, _not_ a geographic copy; `="cross-site"` if `MINIO_SECONDARY_ENDPOINT`). It is **best-effort and non-fatal**: a replica failure does **not** roll back the AIP (`replicado=false` + a `REPLICA` `preservacion_evento` with `resultado=fallo`); `num_copias≤1` → no replica.
- `GET /api/v1/preservacion/aip/{expediente_id}` — the current AIP (requires `USUA_PERM_EXPEDIENTE` `min_crud=1` — it exposes `premis_xml` with document names/hashes and the WORM location). The WORM location fields are `null` in AIP rows predating Increment B (backward compatibility).
- `POST /api/v1/preservacion/proteger-indice` `{file_id, expediente_id, retain_until? | (retencion_anios + fecha_inicio)}` — **(E10 Increment A, [ADR-023](adr/ADR-023-worm-object-lock-preservacion.md))** protects the **signed electronic index** (already uploaded to `files`, referenced by `file_id`) under **WORM / Object-Lock (S3)**. It provisions (idempotently) the per-tenant preservation bucket `orpycamcp-{slug}-preservation` (name taken **only from the JWT claim**), **verifies fixity** (recomputes the SHA-256 over the bytes and checks it against the hash **recorded at the original upload** — the caller declares **no** hash; mismatch → `409` + a failed `FIJACION` event, nothing is uploaded), copies the object with per-object **`Retention(COMPLIANCE, retain_until)`** , records it in `preservacion_worm_objeto` and emits `WORM`/`FIJACION` PREMIS events plus an `audit_log` entry. **Retention is supplied by the caller**, derived from the TRD (storage never calls archive): either `retain_until` directly **or** `retencion_anios` (1–100) + `fecha_inicio` (calendar-year arithmetic), never both. **Only `X-Internal-Token` (D-02, AREA-08-16)**: it is no longer authorised by a user JWT and the gateway **does not route it** (an allow-list of `/preservacion/` sub-routes); archive-service calls it with the signed identity of the closing user. **Idempotent (E10 Inc.1 wiring)**: `201` with the WORM location on first protection; **`200`** with the existing row if that expediente's index is ALREADY protected (it does not re-upload — a short circuit before `put_object`, backed by the partial unique index `WHERE tipo='indice'` and a **per-`(tenant, expediente)` session advisory lock** that serialises `check→put→insert`, so no duplicate irreversible WORM objects are created under a race). `400` invalid retention (missing, in the past, out of bounds, or conflicting sources); `404` if the `file_id` does not exist in the tenant; `409` fixity mismatch or `worm_singleton_conflict` (that `(expediente, tipo)` is already protected with a **different** `file_id`; nothing is uploaded); `422` if the body carries `legal_hold`; `503` if `PRESERVATION_ENABLED=false`. **Trust boundary**: the `file_id ↔ expediente_id` correspondence (that `file_id` really is that expediente's signed index) is guaranteed by **archive-service** (its only caller); storage validates that the `file_id` exists but does not model expedientes. **Effect on disposition**: while the index is under a future `retain_until` or a `legal_hold`, the expediente's final disposition stays **blocked** (destruction cannot happen before the retention expires — Law 594/2000, AGN Agreement 060/2001).
- _(Internal, service-to-service; **not** reachable from the gateway — `X-Internal-Token` is not in `FORWARDED_REQUEST_HEADERS`)_ `POST /api/v1/preservacion/proteger-indice-internal` — the **internal** twin (D-02) of `proteger-indice` with the SAME service and idempotency, authenticated **only** by `X-Internal-Token` instead of `require_permission(crud≥3)`, and attributed to the system principal `SYSTEM_RECONCILER_ID`. It exists because the **WORM wiring of the index** (`IndexService.proteger_worm_indice`, archive-service, E10 Inc.1) has two origins: the best-effort synchronous attempt right after signing the index on closure (`origen="close"`, a real user → the public route) and the **3rd reconciliation phase** (`run_once_worm`, `origen="reconciliation"`, `SYSTEM_RECONCILER_ID` with no `auth_users` row → this internal route; `require_permission` would always reject it). Retention is derived from the expediente's TRD (`archivo_gestion_years + archivo_central_years`, `fecha_inicio`=closure); **fail-closed if the TRD does not resolve** (it does not protect, marks `trd_sin_retencion`, recoverable by the sweep). It mirrors `/signature/internal/sign-indice`.
- `POST /api/v1/preservacion/proteger-artefacto` `{file_id, expediente_id, tipo, retain_until? | (retencion_anios + fecha_inicio)}` — **(E10 Inc.2 wiring, [ADR-023](adr/ADR-023-worm-object-lock-preservacion.md))** the **generalised** form of `proteger-indice`: it protects any **preservation artifact** of the expediente under WORM/Object-Lock, with `tipo ∈ {indice, acta_transferencia, acta_eliminacion, acta_firma_elabora, acta_firma_entrega, acta_firma_recibe}` (defaults to `indice` if omitted; the three `acta_firma_*` are the handover record's **personal signatures** —AREA-07-10, storage migration `018`—, one type per role because protection is a singleton per `(expediente, tipo)`; they are control instruments: they do not postpone disposition of the content, but `legal_hold` covers them). SAME service, fixity, caller-derived TRD retention, `X-Internal-Token` only, and **idempotency** as `proteger-indice`, now keyed **on `(expediente_id, tipo)`** (partial unique index `WHERE tipo='acta_transferencia'` on `preservacion_worm_objeto`, migration `010`) and with the session advisory lock keyed on **`(tenant, expediente, tipo)`** — two artifact types of the same expediente (index and acta) do not collide, but each serialises its own `check→put→insert`. `/proteger-indice` is kept as a **hard alias** that forces `tipo="indice"` whatever the body says (Inc.1 backward compatibility). Status codes and trust boundary are identical to `proteger-indice`. The **signed transfer record** (XAdES seal, `transferencia_acta_firma`) is wired by archive via `TransferenciaService.proteger_worm_acta` (best-effort synchronous trigger after sealing + the 4th reconciliation phase `run_once_actas_worm`); its retention derives from the TRD of the transfer's expediente with **`fecha_inicio = firmado_at` of the acta** (not the closure date — an acta may be signed much later in secondary transfers).
- _(Internal, service-to-service; **not** reachable from the gateway)_ `POST /api/v1/preservacion/legal-hold-internal` `{expediente_id, activo, motivo (≥10), acta_ref}` — **(AREA-08-07)** the only way to **set or lift** the legal hold of every WORM object of an expediente. `legal_hold` is **no longer a field** of `aip`/`proteger-*`/`renovar-retencion-internal` (a freeze set at protection time had no inverse act and blocked final disposition forever; `422` if sent). It calls `enable/disable_object_legal_hold` on each row's exact `version_id` (outside the tx) and, **in one `conn.transaction()`**, updates `preservacion_worm_objeto.legal_hold` and writes `worm.legal_hold_fijado` / `worm.legal_hold_levantado` (with `motivo` and `acta_ref`). Idempotent: rows already in the requested state are not touched. `200 {expediente_id, activo, objetos_afectados, objetos_sin_cambio}`; `404 worm_object_not_found`; `422` without a motive; `503` if `PRESERVATION_ENABLED=false`. `X-Internal-Token` only. Step order: **set** = store (primary and AIP replica) → row + audit entry in one tx; **lift** = `worm.legal_hold_levantamiento_solicitado` entry → store → row + `worm.legal_hold_levantado` (`worm.legal_hold_levantamiento_fallido` if the store fails; the row keeps blocking and the act is retryable). Serialized by a `(tenant, expediente)` advisory lock; a missing replica is tolerated. The entry carries `copias_alcanzadas`. Orchestrated by `POST /api/v1/expedientes/{id}/legal-hold` (archive-service).
- _(Internal, service-to-service; **not** reachable from the gateway)_ `POST /api/v1/preservacion/proteger-artefacto-internal` — the **internal** twin of `proteger-artefacto` (with `tipo` in the body), authenticated only by `X-Internal-Token`, attributed to `SYSTEM_RECONCILER_ID`. Used by the unattended acta reconciliation (`run_once_actas_worm`, `origen="reconciliation"`), just as `proteger-indice-internal` is for the index. `/proteger-indice-internal` remains the hard `tipo="indice"` alias.
- _(Internal, service-to-service; **not** reachable from the gateway — `X-Internal-Token` is not in `FORWARDED_REQUEST_HEADERS`)_ `POST /api/v1/preservacion/renovar-retencion-internal` `{expediente_id, tipo, retencion_anios + fecha_inicio | retain_until}` — **(E10 debt: WORM retention renewal for Permanent Conservation series)** extends (never shortens) the `retain_until` of an artifact **already protected** under WORM, identified by `(expediente_id, tipo)` (`tipo ∈ {indice, acta_transferencia}`). It reuses `RetentionMixin`. **Monotonic** semantics: if the resolved date does not **strictly** exceed the persisted `retain_until`, it is an **idempotent no-op** (`200`, `renovado=false`, the object store untouched). If it does exceed it, it calls `Minio.set_object_retention` (a real extension; COMPLIANCE would reject a shortening anyway) and answers `200` with `renovado=true`; the UPDATE + the `RENOVACION` PREMIS event + the `file.worm_retencion_renovada` audit entry all go in **one transaction** (the `set_object_retention` sits outside it, before). Authenticated **only** by `X-Internal-Token` (attributed to `SYSTEM_RECONCILER_ID`): renewal is a **system act** driven by archive's 5th reconciliation phase (`run_once_worm_renovacion`), the only component that knows which series are Permanent Conservation — **there is NO public route** (it was withdrawn during remediation: storage cannot enforce the CT filter, so a public route taking a direct `retain_until` would be irreversible surface with no CT guarantee). `404` `worm_object_not_found`; `400` invalid retention; `503` if `PRESERVATION_ENABLED=false`. `retention_mode`/`legal_hold` do **not** change. Response: `{id, expediente_id, tipo, bucket, object_key, retain_until, retention_mode, renovado, renovaciones, renovado_at}`.
- `GET /api/v1/expedientes/indices/pendientes-renovacion?page=&size=` — **(E10 debt: visibility of CT renewal)** lists the WORM artifacts of **Permanent Conservation** series (index + acta) whose renewal is **exhausted** (`worm_renovacion_agotado=TRUE`) or **at risk** (`worm_retain_until` falls due within `worm_renovacion_ventana_dias`) — so that an archivist can see a permanent-conservation COMPLIANCE lock about to expire unrenewed (mirror of `pendientes-firma`). Gate `USUA_PERM_EXPEDIENTE` + **no read-up** (the `nivel_seguridad <= clearance` filter lives in the SQL of both queries; `X-Total-Count` reflects the count **already filtered** by clearance — it is not an oracle). Exhausted first, then nearest expiry. Per row: `{expediente_id, expediente_code, tipo, worm_retain_until, worm_renovacion_intentos, worm_renovacion_agotado, worm_renovacion_ultimo_error}`; `tipo ∈ {indice, acta_transferencia, aip, acta_firma_elabora, acta_firma_entrega, acta_firma_recibe}` — **the XML of each personal signature of the record** (E10 debt, AREA-07-10) also enters CT renewal: the 5th phase sweeps it with its own advisory lock (`worm_renovacion_firma_personal`) and backoff (`transferencia_acta_firma_personal_worm.worm_renovacion_*`, migration `060`), calls `renovar-retencion-internal` with `tipo=acta_firma_<rol>` (storage accepts it now; the row is identified by `(expediente_id, tipo)`), **monotonic** (storage only extends if the resolved date exceeds the current one; the local mirror uses `GREATEST`), and each real extension leaves an `archive.acta_firma_personal_worm_retencion_renovada` entry (system actor). The 5th reconciliation phase feeds it: for every active tenant it sweeps the indexes and actas **of Permanent Conservation (CT) disposition series** whose `retain_until` falls due within `WORM_RENOVACION_VENTANA_DIAS` (default 400 days) and calls the internal endpoint with `retencion_anios=TRD_total` + `fecha_inicio=today` (**rolling window**, never a far-off/9999 date). The CT filter is **fail-closed in archive's own SQL** (`disposition ∈ {CT, conserve}`) — `E`/`S`/`M` series never enter the sweep, even when close to expiry (they expire and are disposed of legitimately). It has its own exponential backoff (`worm_renovacion_intentos`/`worm_renovacion_agotado` in `expediente_indice`/`transferencia_acta_firma`, migrations `031`/`032`) — an `agotado=true` on this axis is a **higher-severity** alarm than on initial sealing/protection: it means the COMPLIANCE lock of a **permanently** conserved record risks expiring without ever having been extended. **AIP is out of scope** for renewal (no mirror in archive-service, future debt).

---

## workflow-service — port 8006

> Requires the `X-Tenant-Slug` and `X-User-Id` headers.

> **Automatic distribution (RF-FLU-04):** in addition to the endpoints, workflow-service consumes the `orpycamcp.document.events` stream. When a document is registered (`document.radicado.created`) it evaluates the active rules and, if any matches, **auto-assigns** the radicado to its dependencia (step + append-only event, author = system). It is idempotent (it does not redistribute if the radicado already has steps).

### `POST /api/v1/workflows/assign`

Assigns a radicado to a dependencia (first step or reassignment). Requires `PERM_TRAMITAR`; `403 forbidden` if the permission or the tenant is missing (RF-SEG-03). **ADR-028 (remediation):** the actor's *clearance* is applied **in the `WHERE`** of the radicado read (RF-SEG-08, no write-up): a nonexistent radicado **or one above their level** gives the same `404 radicado_not_found`. With an active step, **only its holder** may distribute it (neutral `404 no_active_step` for anyone else); **without an active step it is not anyone's either** (R16): with history, whoever held the last step (completed, radicado not terminal); with no step at all, whoever registered it (`radicados.registered_by` = actor; a radicado with null `registered_by` cannot be distributed by hand by anyone). A closed one gives `404 no_active_step`, no longer `409`. Redistributing someone else's step is the cascade reassignment (`USUA_PERM_ADMIN`), not this route. Automatic distribution on registration is a system actor and does not go through these gates.

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

Transfers the radicado to another dependencia. Requires `from_dept`. Requires `PERM_TRAMITAR` (RF-SEG-03). Same clearance and holder gates as `assign`.

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

- `POST /api/v1/workflows/{radicado_id}/vistos-buenos` `{revisores: [uuid,...]}` — creates the ordered review chain (1..20 reviewers, no duplicates). Requires `PERM_RADI` (D-09); `403 forbidden` if the permission, the identity or the tenant is missing. `409 cadena_existente` if the radicado already has a chain. `422 revisor_sin_habilitacion` if any reviewer's clearance is below the radicado's level (their turn would be stuck with `404` in `decidir`); the message does not reveal the level.
- `POST /api/v1/workflows/{radicado_id}/vistos-buenos/decidir` `{aprobar, comentario?}` — the reviewer whose turn it is (lowest pending order) approves/rejects. Requires a valid `X-User-Id` (400 if missing). `403 not_a_reviewer` if the actor is not in that radicado's reviewer chain; `403 no_es_su_turno` if they are in the chain but it is not their turn; a rejection stops the chain.
- `GET /api/v1/workflows/{radicado_id}/vistos-buenos` — chain + global status (`en_revision|aprobado|rechazado`). No-read-up: a radicado above the caller's clearance (or no identity, PUBLIC level) gives `404 sin_cadena`, same as one without a chain. `decidir` and creation apply the same rule (`404 sin_pendiente`/`sin_cadena`).

### `POST /api/v1/workflows/{radicado_id}/devolver` (return to sender)

Re-routes a misassigned radicado: `{ "to_dept": "...", "causal": "DEV-DEP", "comentario"? }`. Closes the active step as `returned` and creates a new one towards `to_dept`, with the reason code in the history. Requires **`PERM_TRAMITAR`** (like `assign`, `transfer` and `complete`; this route and `rollback` were the only two trámite writes in the router with no gate at all). Authorization by tenure on top of the permission: only the current holder of the active step may return it. Holder = the same definition as `finalizar` (assigned to the actor, or an ownerless step of their dependencia) **and** the radicado within their clearance (R15, in the `WHERE`); the step and the holder check are read inside the transaction after the lock (R18). Neutral `404 no_active_step` for a non-holder (there is no `403 not_current_holder` any more), for someone who cannot reach the radicado, and when there is no active step.

### `POST /api/v1/workflows/{radicado_id}/finalizar` (finish processing, ADR-028)

`{ "comentario": "...", "motivo_cierre": "respondido|nrr|traslado_competencia|otro" }` (both **mandatory**; comment 5-500 characters; `422` if the reason is missing or different). Requires **`PERM_TRAMITAR` level 3** (`403` below it: closing is irreversible) and being the **holder** of the active step (`assigned_to` = actor, or an ownerless dependencia step and the actor belongs to it) —or, with no active step, having been the holder of the **last completed step** (they completed theirs and did not distribute) if the radicado is not terminal—. Closes the active step as `completed` and the radicado becomes `closed`, atomically, with a `tipo_tx=finalizar` event (`detalles.motivo_cierre`) and a `workflow.finalizar` audit entry whose payload carries `motivo_cierre`, `step_id`, `flow_event_id`, `tiene_respuesta` (an outgoing radicado with `responde_a` = this one exists), `fuera_de_plazo` (the step's deadline, `due_at` vs. now, wins if present; otherwise the radicado's legal due date, `radicados.due_date` < today; `null` only if neither is known) and `en_expediente` (`null` = unknown, e.g. the tenant does not have that table yet). It is **the only act that closes a radicado** and it cannot be undone. A non-holder, someone below the radicado's level (R15), or a radicado with no active step (including one already closed), gets the same `404 no_active_step` (no oracle). `409 radicado_terminal` if the radicado is `anulado`. Returns the closed step.

### Radicado status and processing acts (ADR-028)

**Current location (ADR-029).** In the same `UPDATE` as `radicados.status` —same transaction, before the audit entry— workflow-service writes `flujo_dept`, `flujo_dept_code` and `flujo_usuario`: the active step (highest `step_number`) or, with no active step, the most recently closed one (`completed_at`, not the step number: after a rollback the cancelled one has the highest number but closed earlier). It covers assign, transfer, return, roll back, complete, finish and the cascade reassignment. Terminal radicados **freeze**: a closed one stays where it was finished, an annulled one where it was when annulled. The code is only resolved when the step's name matches **one** active dependencia; otherwise `flujo_dept_code` is `null` and the name stays as a snapshot. See `GET /api/v1/documents` and [ADR-029](adr/ADR-029-ubicacion-actual-del-radicado.md).

`radicados.status` is written by workflow-service **in the same transaction as the step** —before the audit entry, so if the entry fails everything rolls back—: active step `pending` → `distributed`; `in_progress` → `in_progress` (only via the holder's `marcar_leido`); completing without marking as read leaves `distributed` (declared). `closed` and `anulado` are **terminal**: `assign`, `transfer`, `complete`, `devolver`, `rollback`, `finalizar` and the transactions that move the step answer **`409 radicado_terminal`** (`El radicado está cerrado o anulado y no admite actos de trámite`) and change no step, event or audit entry; `informar`/`agendar` remain allowed. The change goes in the payload of the existing audit entry (`"estado_radicado": {"antes", "despues"}`) and, after commit, `workflow.radicado.estado_cambiado` is published on `orpycamcp.workflow.events` (dedup `{radicado_id}:{despues}:{flow_event_id}`). `GET`s never synchronise. `inbox`, `pending` and the overdue sweep **exclude** the steps of terminal radicados. Outgoing (`S`) and internal (`I`) radicados have no outward distribution: their status only moves when a step touches them. **Annulment cancels the active step** (in the same transaction, `flow_events.tipo_tx=anulacion`).

### Procedure transactions (RF-FLU-01)

- `GET /api/v1/workflows/transaction-types` — catalog (informar, NRR, agendar, no_agendar, change_folder, marcar_leido, validate_trd_send, solicitar_firma, vobo, cerrar_exp, anular…), with their atomic permission, state effect and `requires_holder` (ADR-028, R17: **every** transaction —holder-gated or not— applies the actor's clearance in the read of the latest step: an unreachable radicado gives `404 no_flow`, the same as a nonexistent one or one with no steps, without returning its `tracking_number`/`to_dept`. ADR-028: when `true` —today `marcar_leido` and `anular`— only the holder of the active step may run it; a non-holder gets the same `404 no_active_step` as if there were no step, **also on a terminal radicado** —it is not revealed with a `409 radicado_terminal`—. Besides, **every transaction that moves the step** (`target_step_status`) requires holding it, whether or not its catalog row says so: the catalog is tenant-configurable).
- `POST /api/v1/workflows/{radicado_id}/transactions` `{ "tipo_tx": "anular", "comentario": "...", "detalles": {} }` — executes the transaction: applies the effect to the active step (e.g. `marcar_leido`→in_progress, `anular`→cancelled) and records the append-only event. **Per-type RBAC enforcement (RF-SEG-03):** each type declares in `transaction_types.requires_permission` the permission it demands (`anular`→`PERM_ANULAR`, `cerrar_exp`→`PERM_CERRAR_EXP`, `vobo`→`PERM_VOBO`…); without it, `403 forbidden` and the transaction is **not** applied. A type with `requires_permission = NULL` **falls back to the `PERM_TRAMITAR` floor**, it is not open: `transaction_types` is a tenant catalogue, and leaving the column empty cannot be the easiest way to open a door. All 12 seeded types declare their own, so the floor changes nothing observable today. There is deliberately no fixed route gate: requiring `PERM_TRAMITAR` for all of them would break someone who holds `PERM_VOBO` to sign off and nothing else. `404 unknown_transaction` / `no_flow`. `PATCH /{step_id}/complete` requires `PERM_TRAMITAR`.

### `POST /api/v1/workflows/{radicado_id}/rollback`

`{motivo}` (**mandatory**, 5-500 characters). Requires **`PERM_TRAMITAR`**. Reverts the last assignment (RF-FLU-08): cancels the current step —only if it is still `pending`— and reactivates the previous one, atomically, with a `tipo_tx=rollback` event. The `motivo` is recorded in two traces, atomically with the mutation (a single transaction): `flow_events.comentario` (the operational route map) and `public.audit_log.payload` (the forensic legal trace, the same pattern as assignment). Same holder definition and clearance as `devolver` (R15, R18, R19). `409 no_active_step` (no active step, not the holder, or an unreachable radicado: neutral) or `cannot_rollback_initial` (it is the initial registration). `422` if the `motivo` is missing or below the minimum length.

### `POST /api/v1/workflows/reassign/cascade`

Reassigns, in a single transaction, all the active steps of a user or dependencia to a new responsible party, leaving no orphan radicados (RF-FLU-08).

```json
// Request (provide from_assigned_to or from_dept)
{ "from_dept": "Dirección Jurídica", "to_assigned_to": "uuid" }
// Response 200
{ "reassigned": 12, "skipped_terminal": 0 }
```

ADR-028: reassigning means the new owner has **not read** the radicado (Orfeo `LEIDO=0`): the steps go back to `pending` and the radicado to `distributed`, with one `workflow.estado_radicado` audit entry per radicado whose status changed. **Terminal** radicados (`closed`/`anulado`) are excluded —their step is not reassigned— and counted in `skipped_terminal` (steps skipped). Concurrency: a **single** tenant-scope advisory lock (exclusive here, shared in every processing act) plus row locks ordered by `id` on the radicados —not one advisory lock per radicado, which exhausted `max_locks_per_transaction` on a large sweep—; while it runs, the tenant's other processing acts wait.

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

Marks a step as completed. **ADR-028:** only the **holder** (`assigned_to` = actor, or an ownerless dependencia step and the actor belongs to it; the same definition as `finalizar` and `marcar_leido`) and only an **active** step: someone else's, a nonexistent, an already closed one, **or one of a radicado above the actor's clearance** (R15, in the same `WHERE`) all give the same `404 step_not_found`.

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
- **Accumulated fonds** (ADR-026): `origen: "fondo_acumulado"` requires `fecha_extrema_final` and `trd_serie_id` (of a TVD) from creation; `fecha_extrema_inicial` is optional. **`422`** if either is missing, if **`fecha_extrema_final` is in the future** — it is the base of an **irreversible COMPLIANCE** Object-Lock `retain_until` (`schemas/archive.py`): a future date would block the fonds' disposition for decades — or if `fecha_extrema_inicial > fecha_extrema_final`. With `origen: "corriente"` (the default) the extreme dates are **not** accepted (`422`). The same rule applies to bulk load.

#### `POST /api/v1/expediente-metadata/templates`

Defines the metadata template (JSON Schema) of a TRD series. A single active one per series. `422 invalid_json_schema` / `409 conflict`. `GET` lists (filter `?trd_serie_id=`).

#### Documentary transfers (E12)

> **Access control (D-08):** all endpoints require `USUA_PERM_EXPEDIENTE`. Without it: `403 {"error": "forbidden"}`.

- `POST /api/v1/transferencias` `{tipo: primaria|secundaria, expediente_id, ubicacion_origen_id?, ubicacion_destino_id?}` — creates (precondition: expediente `closed` **with its electronic index `firmado`**: `409 expediente_not_closed` if it is not closed and **`409 indice_no_firmado`** —AREA-07-03— if the current index is not signed, with a `transferencia_preparada_failed` denial entry; only `recibir` used to demand it and the defect travelled to the last phase). Cycle `preparada→enviada→recibida|rechazada`. Every act (create/send/receive/reject) leaves an immutable `audit_log` entry (both success and denial) with the actor identified unambiguously by `X-User-Id` (a UUID; no fallback to username). **Integrity (E12 H-L/H-M)**: at most **one active transfer** (`preparada`/`enviada`) per expediente — a second one gives `409 transferencia_activa_existente` (partial unique index; after a `rechazada` it can be re-prepared); and if `ubicacion_origen_id`/`ubicacion_destino_id` are supplied they must go **together** and be **coherent with the type** (`primaria`: origin `gestion`→destination `central`; `secundaria`: `central`→`historico`; Law 594/2000 art. 23), with `origen != destino` — otherwise `422` (`ubicaciones_par_incompleto` | `ubicaciones_origen_destino_iguales` | `ubicacion_not_found` | `ubicacion_incoherente_con_tipo`). **Mandatory only where there is physical custody**: if the expediente has a located physical placement (via `expediente_unidad`, which is M2M), the pair is **mandatory** (`422 ubicaciones_requeridas_fisico`) and `ubicacion_origen_id` must **check out** — it must be among the expediente's current locations (`422 ubicacion_origen_no_actual`; any of the M2M ones is valid); if the expediente is purely **electronic** (no physical units), the transfer is **logical and carries NO pair**: supplying it with no custody to back it is **`422 ubicacion_sin_tenencia`** (AREA-07-06; it used to be accepted unchecked and the FUID handover record attested a movement no link supported). The custody is read **inside the act's transaction with `FOR SHARE`** (not before: a unit moving between the check and the `INSERT` left the record checked against a location that was no longer the current one). If the location is deleted in the race, the FK no longer gives a `500` with no audit entry: it is `422 ubicacion_not_found` with an entry (AREA-07-08). The destination is not checked against anything (only type coherence — it is where it is going). Reading the physical topology needs no clearance (custody is orthogonal). `transferencias.objeto` is mandatory **in the database** too (a `CHECK` since migration `055`, new rows only: history stays operable).
- `POST /{id}/enviar`, `POST /{id}/recibir`, `POST /{id}/rechazar`. On **receipt**, within a single transaction: the holdings' **fixity is re-verified** (SHA-256 of each document vs the index's hash + re-hash of the index XML; `409 fixity_mismatch` rolls back without freezing if the holdings were altered between closure and receipt — the response carries only `num_documentos_fallidos`, the per-document detail stays in `audit_log`), the expediente moves to `transferred` (frozen), and the **handover record (acta) is frozen** (see `/{id}/acta`). **Rejection** requires `{motivo}` (mandatory, `min_length=3`, non-empty → `422`); the reason is stored in its own column without overwriting the preparation note.
- `GET /api/v1/transferencias?estado=&tipo=`, `GET /{id}`, `GET /{id}/fuid` (the expediente's **live** FUID, regenerated on the fly).
- `GET /{id}/acta` (`?verify=true`) — the handover record **FROZEN** at receipt (E12 H-I, AGN Agreement 001/2024 FUID Annex; Law 594/2000 arts. 15/26). It returns the immutable snapshot written on receipt (it never regenerates it): a `TransferenciaActaResponse` with the canonical FUID XML, its `acta_fuid_sha256` (the acta's fixity, recomputable by the client), the three responsible parties `elaborado_por`/`entregado_por`/`recibido_por` with their dates, and the transferred `indice_version`. **AREA-07-04** (migration `053`): also **`indice_firma_id`** (the XAdES seal covering the delivered index) and **`indice_sha256`** (the hash of the XML that seal covers), read from the **same row** of `expediente_indice` the fixity used and embedded in the XML that is hashed and sealed (`<indiceElectronico version firmaId sha256>` in the header); an index version that is not signed prevents receiving (`409 indice_no_firmado`). `null` in earlier records (no link is invented in an already-sealed record). **AREA-07-11**: **`cabecera_completa`** (derived on read): `false` when the frozen XML has an empty administrative unit, producing office or object —records before migration `052` were sealed like that and are not redone—; the gap is declared instead of hidden. **AREA-07-05**: receipt also records its fixity check as a PREMIS `FIJACION` event in storage (post-commit, best-effort, with reconciliation). **`firma` block** (E06 H-H): `{presente, valida, xades_level, acreditado, motivo}` — the acta carries an **institutional XAdES-B/T/LT/LTA + OCSP seal** of the tenant (enveloped over the canonical XML, covering the very bytes whose SHA is anchored in `audit_log`); `presente=true` once sealing converged to `firmado`; with `?verify=true`, `valida` is computed on demand (it downloads the signed XML from the object store and verifies it against signature-service). `acreditado` is **always `false`** (a non-ONAC-accredited seal — a **verifiable** timestamp, not strongly binding; `xades_level` must be read alongside `acreditado`). Sealing is **best-effort** (the signature is additive): if it has not been sealed yet, `firma.presente=false` with a `motivo` (`pendiente_firma`/`sello_ausente`/…) — the acta remains **intact** by fixity + immutability + anchoring. **No read-up** on reading (the acta exposes subject/series/signature = content): a full `404` if the expediente exceeds the caller's clearance — asymmetric with **generation**, which happens under unrestricted clearance (custody). The acta's immutability is **enforced by a trigger** at engine level (`BEFORE UPDATE/DELETE/TRUNCATE`, migration `023`), on top of being verifiable by fixity and anchored in the hash chain.
- `POST /{id}/acta/firmar` — **manual** retry of the XAdES sealing of an acta in `pendiente_firma` (E06 H-H). `USUA_PERM_EXPEDIENTE`, attributed to the human who triggers it (`origen="manual"`). `409 acta_ya_firmada` if already signed (idempotent), `503` if it still cannot be sealed. It **rearms** the reconciliation backoff so the automatic job resumes. The acta's signature also converges on its own via the generalised reconciliation job (at a lower cadence than the index).
- `POST /{id}/acta/firmar-personal` `{totp_code, rol?}` — **PERSONAL PKI electronic signature by a FUID RESPONSIBLE PARTY** (PREPARER "Elaborado por", role `elabora`; SENDER "Entregado por", role `entrega`; or RECEIVER "Recibido por", role `recibe`) over the handover record (E17/F4 Inc.1-3; AGN Agreement 001/2024 FUID Annex; AGN Agreement 042/2002; Law 527/1999 art. 7). An **explicit step after** `recibir()` (never inline: this preserves the custody transaction free of 2FA); the roles are signed **in any order**, each as an independent atomic act. **The role is DERIVED from the frozen identity**: `actor == creada_por` → `elabora`, `actor == enviada_por` → `entrega`, `actor == decidida_por` → `recibe`. The body's `rol` is **optional** and only breaks ties when the same identity holds several roles (self-transfer: `400 rol_requerido` if missing): it NEVER grants a role — a third party who is none of the three responsible parties gets **`403 firmante_no_es_parte`** (and likewise if they request via `rol` a part their identity does not confer), **before** the TOTP is consumed. `USUA_PERM_EXPEDIENTE` + real **no read-up** on the expediente for every role (a neutral `404` above clearance); `409 transferencia_no_recibida` if it is not `recibida` yet. Archive **mediates**: it forwards the responsible party's `Authorization`/TOTP to signature-service `POST /signature/sign-personal` (which verifies-and-consumes the TOTP atomically and signs the frozen `acta_fuid_xml` with their personal certificate). **Idempotent** over `(transferencia_id, rol)`: repeating an already signed role returns `200` with the existing row, without re-signing or re-burning a TOTP. `503 canal_firma_personal_no_disponible` if signature does not answer. Returns a `TransferenciaActaFirmaPersonalResponse` (`rol`, `firmante_id`, `firma_id`, `xades_level`, `acreditado` **always `false`**). Recorded in `transferencia_acta_firma_personal` (**without** unattended reconciliation of the SIGNATURE — it requires an interactive TOTP). **AREA-07-10**: each personal signature's XML enters the **WORM store** (type `acta_firma_<rol>` in storage), post-commit and best-effort, with its own backoff reconciliation (`transferencia_acta_firma_personal_worm`, migration `057`; only the institutional seal had Object-Lock before). If a party cannot sign, see the `firma-inobtenible` act (below). The `acta_firma_personal_require` gate (default `off`) **never blocks `recibir()`** nor another role's signature; it only modulates the compliance verdict.
- `GET /{id}/acta/verificacion` — **aggregate verification** of the acta (E17/F4 Inc.1-3). It composes the verification of the **institutional seal** plus that of **each personal signature** present (downloading the signed XMLs from the object store and verifying them against signature-service). Returns `{sello: {...}, firmas_personales: [{rol, firmante_id, valida, revocacion_status}], completitud, valida, acreditado, auto_traslado, elaborador_firmado, excepciones}`: `completitud ∈ {sellada, firma_personal_pendiente, firmada_receptor, firmada_remitente, firmada_completa, firmada_con_excepcion, sellada_con_excepcion}` (derived, a factual presence — `firmada_completa` = **both PARTIES TO THE HANDOVER** (sender + receiver) signed = **reinforced bilateral attribution**, NOT full legal conformity). The **PREPARER is an ADDITIVE signer** (not a party to the handover): their signature does NOT condition `completitud`, and is reported in the ORTHOGONAL boolean **`elaborador_firmado`**. `valida` = cryptography + revocation of **everything** present (including the preparer's signature, fail-closed → a revoked preparer certificate yields `valida=false` even with `firmada_completa`; the two orthogonality axes differ on purpose), `acreditado` is **always `false`**, and `auto_traslado=true` when the same identity both sent and received (**a compliance observation**: no dual control — the FUID does not require distinct natural persons, so it is flagged but not prevented). **No read-up** identical to `/{id}/acta` (a neutral `404` above clearance; the bodies never enumerate reserved radicados). Also returns `advertencias` (signature `/verify` notices of a still-valid signature, deduplicated), `constancias_sin_firma` (live exceptions predating migration `061`, never reported valid) and `politica_xades` (the ENTITY's XAdES policy used for `firma_personal_pendiente`: tenant parameter first, environment only as fallback). `sellada_con_excepcion` = both parties covered **only by exception** (nobody signed); a **revoked** exception covers nobody. The exception's full `fundamento` is returned **only to `USUA_PERM_ADMIN` level ≥ 3** (sensitive: health, death); everyone else gets `motivo`, `referencia_documental` and `fundamento_sha256`.
- `POST /{id}/acta/firma-inobtenible` `{rol, motivo, fundamento, referencia_radicado_id, referencia_anexo_id, totp_code?}` → `201` record of exception — **administrative act "signature unobtainable"** (AREA-07-10, ADR-030): a party of the handover record (`elabora`/`entrega`/`recibe`) that **cannot sign** (death, retirement, incapacity) is declared explicitly. Gate **`USUA_PERM_ADMIN` level ≥ 3** (not the transfers permission the parties sign with) **and `PERM_FIRMA` ≥ 2** (the seal is stamped in the declarant's name: `403 declarante_sin_permiso_de_firma`). `motivo ∈ {fallecimiento, retiro, incapacidad, otro}`; `fundamento` (20–2000 chars) is mandatory. **The supporting document is no longer free text**: `referencia_radicado_id` (a radicado UUID) and `referencia_anexo_id` (**mandatory** since FIRMA-2: the `file_id` of one of its attachments, whose SHA-256 the server fetches from storage; `503 soporte_documental_sin_huella` otherwise) are **resolved by the server** against document-service with the declarant's clearance — `422 referencia_documental_invalida` (one body for both "does not exist" and "cannot see it"), `503 document_service_no_disponible`; stored canonically as `radicado:<uuid>[#anexo:<uuid>]`. The party's identity is **derived by the server** from the role, never by the client. **The record is EVIDENCE**: an **exclusive-C14N** XML (`urn:orpycamcp:acta-firma-excepcion:v3`: hash of the frozen record, transfer, role, party, reason —`incapacidad` is written `impedimento_sobreviniente`—, commitment to the grounds, support, declarant, instant) **sealed with the institutional XAdES-T seal** (same path as the record; with no seal → `503` and **nothing is declared**) and, if the declarant sends `totp_code` and holds an active credential, **also signed by them** (personal XAdES over the same bytes). The personal signature is **MANDATORY** when the ENTITY's policy (`firma_personal_require_xades`, a tenant parameter) is `todos`, or `clasificados` with an expediente of level ≥ 2 (`422 firma_personal_declarante_requerida` without `totp_code`). Both XMLs enter the **WORM store** (types `acta_firma_excepcion_<rol>` and `acta_firma_excepcion_personal_<rol>` in storage, migration `019`), post-commit and best-effort, with backoff reconciliation and CT renewal (`transferencia_acta_firma_excepcion_worm`, migration `061`). It leaves an **immutable record** (`transferencia_acta_firma_excepcion`, migrations `059`/`061`: append-only by trigger and privileges, one per role) and an `archive.acta_firma_inobtenible_declarada` entry **in the same transaction**; the entry carries the hashes of the grounds and of the record, **not the text**. **Conflict of interest**: the declarant cannot be **any of the three parties** of the transfer (`enviada_por`, `decidida_por`, `creada_por`): `403 declarante_es_parte_de_la_transferencia` with an `archive.acta_firma_inobtenible_denegada` entry; a database trigger repeats it. Mutual exclusion in the **engine**: a party that already signed admits no exception (`409 parte_ya_firmo`) and a party with a **live** exception no longer signs (`409 firma_excepcion_declarada`, **before** spending the TOTP). **Chain (FIRMA-2, ADR-030 addendum, migration `062`)**: over a **live** exception it is `409 excepcion_vigente`; after **revoking** it a new one can be declared (`ordinal` n+1, `sustituye_a` = the revoked one; linear chain enforced by `UNIQUE` constraints) **only if the revocation is sealed and all its artefacts are WORM-protected** (`409 revocacion_sin_proteger`, repeated by a trigger); if the party signed personally — even during the revoked window — the chain is closed (`409 parte_ya_firmo`: the signature prevails). **Self-sufficient record v3** (`urn:orpycamcp:acta-firma-excepcion:v3`): it carries the radicado number and the attachment SHA-256, name and position of declarant and party as a server-resolved snapshot, and the grounds are fingerprinted with a **per-record salted commitment**, `SHA-256(salt_32B || utf8_text)` (audit round, migration `063`): the salt is random, lives in the database (not WORM) and **never** goes into the XML or `audit_log`; a third party verifies it when handed the text and salt of that one record. `fundamento_sha256` and `fundamento_hmac*` are **gone** from the response and the audit entry; the signed XML is the exclusive C14N and `constancia_sha256` its SHA-256. The response carries `fundamento_compromiso`, `fundamento_compromiso_algoritmo` and `fundamento_sal` (admin level ≥ 3 only). All links of a (transfer, role) chain share one WORM retention anchor (the later of the record's seal and the chain's last act); protecting a new link aligns (monotonic, audited) the earlier ones. The `incapacidad` reason travels neutral in XML and audit entry; these artefacts are not published through OAI-PMH, EAD or CMIS. **A declarant holding an active signing certificate always signs personally** (`422 firma_personal_declarante_requerida` without `totp_code`), not only by policy. The `referencia_*` fields are cut by the reader's clearance (`null` if they cannot see the radicado, indistinguishable from "does not exist" or a document-service error). Only on an already-frozen record (`409 transferencia_no_recibida`); real no-read-up on the expediente (neutral `404`). **Effect on `acta_firma_personal_require`**: the exception **covers** its party, but a partial exception does not switch the gate off on its own.
- `POST /{id}/acta/firma-inobtenible/revocar` `{rol, causa, fundamento, totp_code?}` → `201` — **revokes the live exception of a role** (declared by mistake, or the party can in fact sign). Same gate as declaring (`USUA_PERM_ADMIN` ≥ 3) and same conflict-of-interest rule (`403 declarante_es_parte_de_la_transferencia`). It is a **new append-only row** (`transferencia_acta_firma_excepcion_revocacion`, migration `061`): the original record and its Object-Lock are **not touched**, and the `archive.acta_firma_inobtenible_revocada` entry goes in the same transaction. From then on the exception **stops counting**: the party can sign again and `completitud` treats it as uncovered. `404 excepcion_not_found`; `409 excepcion_ya_revocada`. **The revocation is evidence (FIRMA-2)**: `causa ∈ {error_de_declaracion, parte_puede_firmar, otro}` is **mandatory** (`422` otherwise; it travels inside the signed XML); an exclusive-C14N XML (`urn:orpycamcp:acta-firma-excepcion-revocacion:v3`) tied to the hash of the record it revokes, with the **institutional seal** (no seal: `503` and **no row and no audit entry**) and the **personal signature of whoever revokes if they hold an active signing certificate** (`422 firma_personal_revocante_requerida` without `totp_code`); it also requires `PERM_FIRMA` ≥ 2 (`403 revocante_sin_permiso_de_firma`). Its artefacts enter the WORM store (types `acta_firma_revocacion[_personal]_<rol>`, storage migration `020`, with their own reconciliation and CT renewal). After revoking and protecting, a new exception **can be declared** (see the chain above). The response is the same record with `revocada=true`.
- `GET /{id}/acta/firma-inobtenible/{excepcion_id}/fundamento/verificacion` → `FundamentoVerificacion` — **grounds verification** (FIRMA-2; `USUA_PERM_ADMIN` level ≥ 3, no-read-up `404`): recomputes **locally** the commitment `SHA-256(salt || text)` of the stored grounds and checks it against **three independent copies** (the column, the signed XML and the audit entry; and the same for the revocation). A missing or unverifiable copy (`null`) never counts as a match; a pre-v3 record is `verificable=false` (`constancia_sin_compromiso_salado`). It returns neither the text nor the salt, and leaves a `transferencia.acta_fundamento_leido` read entry (as do `GET /acta` and `GET /acta/verificacion` when they serve the plain grounds).
- `GET /{id}/acta/firma-inobtenible/{excepcion_id}/sobre?acto=constancia|revocacion` → `SobreFundamentoResponse` `{excepcion_id, acto, sobre_sha256, xades_level, worm_protegido, xml_firmado}` — **grounds disclosure envelope** (FIRMA-2 round 3, archive migration `064`): the XML `urn:orpycamcp:acta-firma-excepcion-sobre:v1` `{id, acto, texto, salHex, compromiso, algoritmo}` sealed with the institutional seal and kept under Object-Lock as its own object (retention aligned with the chain anchor). **Only read path**: `USUA_PERM_ADMIN` level ≥ 3, no-read-up `404` (and `404 sobre_not_found` for acts older than `064`), `503 sobre_no_disponible` if the object store is down, and the `transferencia.acta_fundamento_leido` entry with `via="sobre"` is written **before** serving it. Never through OAI-PMH/EAD/CMIS or public verification. The record views (`GET /acta`, `/acta/verificacion`, declare, revoke) only add metadata: `sobre`, `sobre_sha256`, `sobre_xades_level`, `worm_sobre_protegido` and the `revocacion_sobre*` counterparts. Grounds verification adds a fourth copy, `sobre` / `revocacion_sobre`. Declare/revoke seal the envelope in the same flow: no seal means `503 sellado_sobre_no_obtenido` and nothing written. `.../worm/reintentar` also resets the `sobre` artefact. `POST .../revocar` no longer **returns** `fundamento` or `fundamento_sal` of the original record (`null`; the full view is `GET /acta`, which records the read).
- `POST /{id}/paquete-entrega` → `application/zip` — **delivery package of the secondary transfer** (B4, ADR-030 §5): `USUA_PERM_ADMIN` level ≥ 3, neutral `404` no-read-up (clearance in the expediente `WHERE`), `409 paquete_solo_secundaria_recibida` unless the transfer is secondary and received, `409 componentes_sin_object_lock` (`componentes`: paths without confirmed Object-Lock, or `aip`; `componentes_clasificados`: count of the `clasificado/...` ones, never listed; fail-closed, nothing is downloaded), `409 componente_integridad_discrepante` (act, seal or component not reproducing its capture fingerprint; writes the `transferencia.paquete_entrega_incidente` audit entry), `503 componente_no_disponible`. It also carries `indice/indice-electronico.xades.xml`, and the note references the AIP (`<aip>`: bucket, key and fingerprint; not embedded in the ZIP). Contents: `acta/` (frozen act as-is, institutional XAdES seal, personal signatures), `clasificado/{constancias,revocaciones,sobres}/` (whole chain, revoked links included; Ley 1712 art. 18 lit. a), `nota-entrega.xml` (references `acta_fuid_sha256`; per-component `ruta`, `sha256`, `clasificacion`, `fundamento`, `acceso`; `sobre="ausente"` for v2/legacy) and `checksums.txt`. The `transferencia.acta_fundamento_leido` (`via="paquete"`) and `transferencia.paquete_entrega_generado` audit entries are written in one transaction before the first byte. Never through OAI-PMH/EAD/CMIS.
- `GET /{id}/acta/firma-inobtenible/{excepcion_id}/fundamento?acto=constancia|revocacion` → `FundamentoLectura` `{excepcion_id, acto, fundamento}` — **on-demand read of the grounds text** (FIRMA-2 round 3; `USUA_PERM_ADMIN` level ≥ 3, no-read-up `404`, `404 fundamento_not_found` if absent): writes the `transferencia.acta_fundamento_leido` entry with `via="lectura"` **before** serving (fail-closed). **Contract change**: `GET /acta` and `GET /acta/verificacion` no longer return `fundamento`, `fundamento_sal`, `revocacion_fundamento` or `revocacion_fundamento_sal` (always `null`), so visiting the screen no longer records a read. **The salt is returned by no read path**: it only travels inside the sealed envelope. Grounds verification is unchanged.
- `POST /{id}/acta/firma-inobtenible/{excepcion_id}/worm/reintentar` and `.../{excepcion_id}/revocacion/worm/reintentar` → `{excepcion_id, acto, repuestos, artefactos}` — **exit from `agotado`** of the record's / the revocation's Object-Lock (audit round A4; `USUA_PERM_ADMIN` level ≥ 3): resets exhausted artefacts to pending with an atomic audit entry, retries the protection and returns each artefact's real state.

#### Physical archive (E17, ADR-017)

> **Access control (D-08):** all endpoints require `USUA_PERM_EXPEDIENTE`. Without it: `403 {"error": "forbidden"}`. `GET /expedientes/{id}/unidades` requires it too; the frontend calls it via `Promise.allSettled` and treats the 403 as a facet error without blocking the view.

- `POST/GET /api/v1/ubicaciones`, `GET /api/v1/ubicaciones/{id}` — recursive topology (site→…→drawer); `codigo` unique per tenant; derived `ruta`. Filters `?parent_id=&activo=`.
- `POST/GET /api/v1/unidades`, `GET /api/v1/unidades/{id}` — conservation units (box|folder|…); when `ubicacion_id` is provided, the **topographic signature** is derived (`DEP01-E05-C0124`, unique). Filters `?ubicacion_id=&tipo=`.
- `POST /api/v1/unidades/{id}/expedientes` — link an expediente to a unit (`folio_inicio/fin`).
- `GET /api/v1/unidades/{id}/expedientes` — what the unit contains; **filtered row by row** by clearance (RF-SEG-08, no read-up): enumerating a unit's contents reveals the _existence_ of its classified expedientes, so a non-reader does not see those with `nivel_seguridad >` their clearance (Law 1712/2014 art. 19; _fail-closed_ to PUBLIC, `403` without `X-User-Id`). `GET /api/v1/expedientes/{id}/unidades` — where the expediente is (signature + range); **deliberately without a clearance filter**: it is queried by an already known `expediente_id` and returns only the physical location (signature/folios, not subject/series) — custody management, orthogonal to read clearance (the same criterion as transfers).
- **Loans (RF-ARF-07)**: `POST /api/v1/unidades/{id}/prestamos` (409 if already lent out), `POST /api/v1/prestamos/{id}/devolver`, `GET /api/v1/prestamos?estado=prestado|devuelto|vencido`, `GET /api/v1/unidades/{id}/prestamos`. `vencido` is derived from `fecha_devolucion_esperada`. Both listings **paginate** (`?page=1&size=20`, max 100) and emit `X-Total-Count`; `GET /unidades/{id}/prestamos` did not until T16 / AREA-07-19 (it returned a unit's whole history on the argument that "the object bounds it", a bound assumed rather than enforced), and its total is that unit's. **Who lent/returned (T6 / AREA-02-08)**: `movimiento_fisico.performed_by` stores the **UUID** from `X-User-Id`, not the `X-Username` login (Keycloak lets an administrator rename a user and names are reassignable; a custody trail attributed to a name stops being reconstructable). Same for `generado_por` of `POST /expedientes/{id}/indice/rebuild`. Earlier rows keep the text they had.
- **History (RF-ARF-08)**: `GET /api/v1/unidades/{id}/movimientos` — append-only movements (RETIRADO/DEVUELTO/…).
- **FUID (RF-ARF-10)**: `GET /api/v1/fuid` — documentary inventory (expediente↔unit↔signature↔series) in JSON with consecutive ordering and AGN 042/2002 fields; filters `?ubicacion_id=&trd_serie_id=`. `GET /api/v1/fuid.xml` exports as XML. **Form header** (AGN Agreement 042/2002, Annex): `entidad_productora` is resolved **server-side** from `public.tenants` — it heads an official archival document, and a free-text field would turn one institution's inventory into any other's — while `unidad_administrativa`, `oficina_productora` and `objeto` arrive as query parameters and are **declarative**: whoever compiles the inventory fills them in, just as on paper. Every row also carries `frecuencia_consulta` (column 9 of the form: `alta`/`media`/`baja`), **derived** from access entries recorded in `audit_log` over the last 12 months rather than estimated — the count is scoped by `tenant_slug`, because `audit_log` is the only table shared across institutions and expediente codes are consecutives that collide between them. **Access control (RF-SEG-08, no read-up):** the FUID describes **content** (`asunto`/series/`signatura`), so it is filtered **row by row** by the expediente's classification — each user only sees in the inventory those with `nivel_seguridad ≤` their clearance (`total` counts only what is visible; _fail-closed_ to PUBLIC, `403` without `X-User-Id`). In `GET /transferencias/{id}/fuid` (a single expediente) the filter is a total `404` if the expediente exceeds the clearance (indistinguishable from non-existent). The transfer itself (create/send/receive/reject) is an act of **custody**, orthogonal to read clearance (it does not filter by it, as with expediente closure/transfer).

#### TRD/CCD — the TRD as a validated instrument (E04, [ADR-025](adr/ADR-025-trd-instrumento-convalidado-versionado.md))

> **Access control (archival configuration):** config **mutations** (`POST/PATCH /api/v1/trd`, `POST /trd/{code}/versiones`, the validation circuit below, `POST/PATCH/DELETE /api/v1/tipos-documentales`, `POST /api/v1/expediente-metadata/templates`) require **`USUA_PERM_TRD`** (fail-closed: `403` without tenant/user-id/permission) and leave an `audit_log` entry. **Config disposition** — `DELETE /tipos-documentales/{id}`, `PATCH /trd/{id}`, `POST /trd/{code}/versiones` and the validation circuit — requires **`crud≥3`** (RF-SEG-03). The `GET` **reads are not all alike**, and since 2026-08-28 the code says so (§4.bis of the test plan). The **classification vocabulary** — `GET /api/v1/trd`, `GET /trd/{id}`, `GET /trd/{id}/retention` and their TVD twins, plus `GET /api/v1/tipos-documentales` and `GET /api/v1/expediente-metadata/templates` — is **open to any authenticated user of the tenant, by declared decision**: `/radicar` and `/expedientes` consume it in order to classify, and `USUA_PERM_TRD` belongs to whoever **administers** the instrument, not to whoever uses it — requiring it here would stop every ordinary officer from registering — and the validated instrument must be published anyway (Ac. AGN 004/2019, compiled by 001/2024; Ley 1712/2014 art. 9). The **circuit** does require **`USUA_PERM_TRD`** (`crud≥1`): `GET /trd/revision-pendiente` and `GET /trd/{code}/versiones` + `GET /tvd/{code}/versiones`, because the history returns the drafts, the `acta_comite`, the `fecha_aprobacion_comite`, the `instancia_convalidante` and the `created_by` of every version — the Committee's deliberation, not the published instrument.
>
> **Instrument state machine** (ADR-025, AGN Agreement 001/2024 which compiles 004/2019): `borrador → aprobada → convalidada`, with `migrada` as a backfill state (series predating migration 037, "the system does not know whether they were validated") that can be **ratified** straight to `convalidada` without passing through `aprobada`, and `aprobada → borrador` (**return**, migration 038) as an honest path for returning with observations. `borrador`/`aprobada`/`derogada` do **not** govern a closure (`409 trd_version_no_vigente`); `convalidada`/`migrada` do. Effective destruction (final disposition, no job yet) will require the frozen snapshot to describe a validated instrument — `convalidada`, or `derogada` with an administrative act (D5, gate corrected in 038: see `app/core/trd_disposition.py`) — the rest of the rules live in the ADR.

- `POST/GET/PATCH /api/v1/trd` — documentary series and **subseries**. Fields: `code`, `name`, `parent_id` (subseries), `archivo_gestion_years`/`archivo_central_years` (two-phase retention), `disposition` (AGN: `CT`/`E`/`S`/`M`), `version`, `valid_from`, `estado`, `valid_to`, `acto_administrativo`, `acta_comite`, `fecha_aprobacion_comite`, `fecha_convalidacion`, `instancia_convalidante`, `rusd_radicado`, `fecha_publicacion`, `supersede_a`, `created_by`. `POST` always creates **`version=1` in `borrador`** (`estado` is not settable by the client). **Two retention field sets (AREA-06-07)**: the calendar calculation uses the two phases (`archivo_gestion_years`/`archivo_central_years`); `retention_years`/`total_retention` are the **legacy** set and the response **derives** them from the phases when both are set (`retention_years` = management, `total_retention` = management + central), so they never disagree. `PATCH` now accepts the two phases; a `PATCH` of only the legacy set is translated into them as on creation, and the `UPDATE` rewrites the derived legacy columns. The phases are substantive: over a non-`borrador` version they give `409 trd_version_inmutable`. `PATCH` is **append-only** (D1): only applicable over `borrador` — any other state answers `409 trd_version_inmutable`, unless the only field sent is `pdfa_profile` (technical preservation policy, editable in any state). `is_active` is no longer accepted in `PATCH` (retiring a version is `derogar`, below).
- `POST /api/v1/trd/{code}/versiones` — clones the code's **current** version (`convalidada`/`migrada`) as a **new** row `version+1` in `borrador`; if the code has no current version (all its versions are `derogada`), it falls back to the **highest version number, in any state** (migration 038 — a derogated code with no successor is never left without a way back). `404 trd_code_not_found` only if the code never existed; `409 trd_version_en_tramite` if there is already a `borrador`/`aprobada` version in progress.
- `GET /api/v1/trd/{serie_id}/retention?closed_at=YYYY-MM-DD` — **retention calendar**: `fin_archivo_gestion`, `fin_archivo_central` and final disposition (`disposition_code`/`disposition_label`) derived from the series and the closing date, plus the **instrument's provenance** (`serie_version`, `serie_estado`, `acto_administrativo`, `fecha_convalidacion`, `instancia_convalidante`, `rusd_radicado`) — declared exactly as it stands in the row, never asserting a validation that did not happen (RF-FIR-15).
- `GET /api/v1/trd/{code}/versiones` — **(ADR-026; the twin declared by ADR-025 and never implemented)** the complete history (any state) of a code, most recent first; an empty list if the code never existed (not a `404`).

**Validation circuit (ADR-025 D2/D6, Increment 2 + migration 038)** — a state machine isolated in the service layer, validated before touching the DB (`409 trd_transicion_invalida` on any transition outside those listed):

#### `GET /api/v1/trd/disposicion-vencida` — due-disposition queue (RF-TRD-08, 2026-08-28)

`?fase=central|gestion` (default `central`) · `?disposition_code=CT|E|S|M` · `?page` · `?size` · `X-Total-Count`. Requires **`USUA_PERM_TRD`**.

Expedientes whose retention calendar has come due, read from the **snapshot frozen at closing time** (`expedientes.disposition`) and never from the live series row: the calendar governing an expediente is the one frozen for it, and a later validation neither advances nor delays a due date already fixed (ADR-025 D3/D4). `fase=gestion` looks at `fin_archivo_gestion` (transfer to the central archive); `fase=central` looks at `fin_archivo_central` (**final disposition**).

Every row carries `accion` (what is due, derived from the disposition code: **a due date is not an order to destroy** — under `CT` it means transfer to the historical archive and preserve permanently) and `gobierna_eliminacion`, which answers **exactly what** `assert_disposicion_final_autorizada` will answer once the executing arm exists, because both call the same predicate.

**No read-up (RF-SEG-08) under the same condition as the rows**, `COUNT` included: narrowing only the page would let the total betray how many classified expedientes are due. Leaves an aggregate query trace (RF-BUS-10).

> **This endpoint executes nothing.** Effective deletion (RF-TRD-07) is still unbuilt.

#### `POST /api/v1/trd/disposicion-vencida/{expediente_id}/decidir` — the confirmation (RF-TRD-06/08, 2026-08-28)

Body `{fase, decision, motivo?, revisar_de_nuevo_el?}` · `201` · Requires **`USUA_PERM_TRD` with `crud≥3`** and an accountable actor (fail-closed `X-User-Id`).

"No destruction is automatic without an authorized user's confirmation" (RT 04, rule 4). Two decisions, both **non-destructive**: `confirmada` records that the TRD action is due (**deletes nothing**: the response carries `ejecutada: false`), and `aplazada` (Model SGDEA R.4.9) requires a reason (≥10 chars) and a **future** review date — a deferral with no return date is forgetting by a nicer name.

**Not accepted from the client**: the due date and the disposition code do **not** travel in the body — they come from the frozen snapshot; otherwise confirming a deletion would be a matter of sending the right date. The expediente is resolved **narrowed by clearance**, and one out of reach answers `404`, not `403`.

If the action ends in destruction (`E`, `S`, `M`), the frozen instrument must govern it: `assert_disposicion_final_autorizada` (ADR-025 D5) → `409`. **`CT` is exempt**: total preservation does not end in destruction. The decision lands in the append-only `disposicion_decisiones` table (migration `046`) and in the immutable `audit_log`, **in the same transaction**.

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

> **Profile v4 "fully conformant" (E15, [ADR-022](adr/ADR-022-indice-electronico-v2-metadatos.md); XSD `schemas/indice-v4.xsd`):** new indexes are generated in `urn:orpycamcp:indice:v4`, which **closes the 4 residues of v3**. **Per document**: RT-15 metadata (`<formato>`/`<tamano>`/`<folios>`/`<fechaIncorporacion>`), a **stable `<orden>`** (immutable), **`<dependenciaProductora codigo="N">`** (supplied at link time; **omitted** on legacy rows without the datum) and **`<fechaDeclaracion>`** (= `added_at`, declaration ≡ incorporation). **In the header**: `<contexto>` (`fechaPrimerUso` + series/subseries by name), **`<listaControlAcceso>`** with `<nivelSeguridad>` + **`<politicaAcceso modelo="no-read-up" requiere="USUA_PERM_EXPEDIENTE" clearanceMinimo="N">`** (a stable, portable policy — the **live RBAC matrix is NOT embedded**, it is queried through an endpoint), **`<pistaAuditoria registro="public.audit_log" objetoRef cadenaVerificable>`** (it attests to the trail, it does **not** embed live entries) and **`<conformidad perfil="v4" productoraPresente pistaAccesoDesde>`** (a per-instance marker; `productoraPresente="false"` over an empty set). The v4 **content_hash canonicalises the whole tree** (v3 covered only `<documentos>`). **Excluding** a radicado is a **soft delete** (`<documento excluido="true">` with its ordinal + `<fechaExclusion>`/`<causalExclusion>` → a **traceable gap**, AGN 001/2024 art. 4.3.2.3). Indexes in v1/v2/v3/v4 that are **already signed remain immutable** (`verify` is version-agnostic). **Label**: "fully conformant — **prospective applicability**" (legacy instances may omit the producing dependencia and their access trail starts at instrumentation; every index self-declares this in `<conformidad>` — RF-FIR-15, do not over-declare). **All** index reads require `USUA_PERM_EXPEDIENTE` + clearance (no read-up, RF-SEG-08) and **leave an access entry** in `audit_log` (`archive.indice_consultado`, real actor only + channel `api|ui|mcp`). **AREA-07-01**: the XML is validated against its XSD (lxml, `app/services/index_xsd.py`, schema chosen by namespace v2/v3/v4) **before the seal is requested** —on close, on manual retry and in reconciliation—: if it does not validate, the index stays `pendiente_firma` with reason `xsd_invalido` (the first error goes in the audit entry) and is **not signed**; a namespace with no XSD is not judged. When sealing is **exhausted** (`reconcile_agotado`), whoever can repair it is told by email (`USUA_PERM_EXPEDIENTE` + `PERM_FIRMA` and the expediente's clearance; the email carries no codes or subjects), besides the `archive.indice_reconcile_agotado` entry (AREA-07-03). Legacy links of **open** expedientes with no producer/format/size are completed with what document-service knows (NULL columns only, one attempt per link, `archive.vinculo_origen_completado` entry; closed ones are evidentiary and untouched).

- `GET /api/v1/expedientes/{id}/indice` (`?version=N`) — current index or by version: `{version, estado, num_documentos, xml_sha256, algoritmo, items[]}`. Each item includes `formato`, `tamano_bytes`, `folios`, `fecha_incorporacion` (null if not declared at link time).
- `GET /api/v1/expedientes/{id}/indice.xml` (`?version=N`) — XML body (`application/xml`, namespace `urn:orpycamcp:indice:v4` on new indexes; `v1`/`v2`/`v3` on already-signed ones).
- `GET /api/v1/expedientes/{id}/export.zip` — **export of the expediente to ZIP** (E02/export, Law 594/2000 art. 19). A CONSULTATION/DELIVERY copy (distinct from the E10 preservation AIP and from the E12 FUID transfer). The ZIP contains: `indice-electronico.xml` (E15, if signed — best-effort), `manifiesto.csv` (both **linked and excluded** radicados —the latter with their reason, without bytes—, with `orden_documental`/`folios`/`formato`/`dependencia_productora`/`fecha`), `documentos/{tracking}/{idx}-{filename}` (the attachment bytes), `LEEME.txt` (a scope legend) and `checksums.txt` (SHA-256 per member, to validate integrity). Requires `USUA_PERM_EXPEDIENTE`. **PER-RADICADO no read-up** (RF-SEG-08): `404` if the expediente exceeds the caller's clearance, and **individual radicados above the clearance are omitted entirely** (not even their existence is enumerated in the manifest) — an expediente-level gate is not enough. Archive orchestrates; the byte assembly is done by storage-service (an internal D-02 endpoint, unreachable from the gateway) without persisting anything; `arcname` is sanitised against zip-slip. The export leaves an aggregate audit entry (`archive.expediente_exportado`, RF-BUS-10). The ZIP is built in an on-disk temp file (no memory cap; `EXPORT_MAX_BYTES` limit, 4 GiB) and served in chunks with `Content-Length`; errors before the first byte: `413 export_demasiado_grande`, `507 espacio_insuficiente`, `502 export_no_disponible`. _Debt: combined PDF._
- `GET /api/v1/expedientes/{id}/historial-acceso` (`?page&size`, E15 v4 R3) — the expediente's **access trail**: it reads `public.audit_log` (read-only, with an explicit `tenant_slug` filter) filtered by `object_ref = code` and `object_type ∈ (expediente, expediente_indice)`, ordered `ts DESC`, with `X-Total-Count`. Returns `{ts, actor, canal, action, ...}`. Requires `USUA_PERM_EXPEDIENTE` + no read-up (**404**, not 403, if the expediente exceeds the caller's clearance — it does not leak existence).
- `GET /api/v1/expedientes/{id}/roles-autorizados` (E15 v4 R4) — a **live snapshot** (unsigned) of the access matrix: the groups that hold `USUA_PERM_EXPEDIENTE` (`crud≥1`) **and** `role_clearance.max_level ≥` the expediente's `nivel_seguridad`, as `[{grupo, clearanceMax, crud}]` sorted by name. Same gate (`USUA_PERM_EXPEDIENTE` + no read-up). This is the datum the signed index deliberately does **not** embed (avoiding churn in the evidentiary artifact and IAM topology leakage in AGN transfers).
- `GET /api/v1/expedientes/{id}/indice/versions` — version history (append-only) with `X-Total-Count`.
- `GET /api/v1/expedientes/{id}/indice/verify` (`?version=N`) — verifies integrity: it checks the index's `valor_huella` against each document's current hash **and** re-hashes the stored XML against `xml_sha256` (`huella_indice_ok` — detecting alteration of the index XML itself, even unsigned). With an XAdES-B signature it downloads the signed XML from the object store and verifies it against signature-service — the `firma` block includes `{presente, valida, nivel_conformidad, acreditado, motivo, advertencias}` (`motivo='signed_xml_unavailable'` → `valida=false`; `advertencias` propagates signature's `/verify` notices). `valido` is the conjunction of per-document fixity + `huella_indice_ok` + the signature (if present). Requires `USUA_PERM_EXPEDIENTE`.
- `GET /api/v1/expedientes/indices/pendientes-firma` — an operational list (paginated, `X-Total-Count`) of the indexes in `pendiente_firma` belonging to `closed`/`transferred` expedientes, with their reconciliation counters (`reconcile_intentos`, `reconcile_agotado`, `reconcile_ultimo_error`, version, `expediente_code`) — direct visibility for the archivist of indexes not yet sealed, **including the `reconcile_agotado=true` ones** that need human intervention (provision the seal + manual retry). **No read-up**: filters by `nivel_seguridad <= clearance` of the caller (in both `COUNT` and rows), so a low clearance does not see indexes of classified expedientes. Requires `USUA_PERM_EXPEDIENTE` (E15/E06 Inc.8).
- `POST /api/v1/expedientes/{id}/indice/firmar` — **manual** retry of the XAdES sealing of an index in `pendiente_firma` (requires `USUA_PERM_EXPEDIENTE`+`PERM_FIRMA`, attributed to the human). `409 indice_ya_firmado` if already signed (idempotent), `503 index_signing_failed` if it still cannot be sealed. A manual retry **rearms** the reconciliation backoff (`reconcile_agotado`→false) so the automatic job resumes once the root cause is fixed. The `archive.indice_firmado` entry carries `origen="manual"`.
- `POST /api/v1/expedientes/{id}/indice/rebuild` — regenerates (a new version if the set or the metadata changed; `{unchanged:true}` if identical). **`409 expediente_not_open`** if the expediente is not `open`. **Version 0** is created automatically when the expediente is created. Requires `USUA_PERM_EXPEDIENTE`. Each regeneration leaves an `archive.indice_regenerated` entry (with `xsd_version`) in `audit_log`.
- When linking a radicado (`POST /api/v1/expedientes/{id}/radicados`) you may **declare** (the server resolves and cross-checks them: see the next bullet) `content_hash` (SHA-256, `valor_huella`), the RT-15 metadata **`formato`** (MIME), **`tamano_bytes`** and **`folios`** (optional, `ge=0`) and, in **v4**, the **`dependencia_productora_codigo`** (INTEGER) + **`dependencia_productora_nombre`** (text) — optional but **together or not at all** (`422` on the partial case). An immutable snapshot is persisted per index version, so `GET /indice?version=N` shows the producing dependencia as it stood when that version was generated. Linking/excluding regenerates the index automatically; on a non-`open` expediente it returns `409`.
- **Linking does not seal what the linker writes (2026-10, T6 / AREA-07-02 and AREA-06-08).** The index is signed with XAdES, and until now "the explicit wins": radicado number, typology, producing dependency, format, size and hash were accepted as sent, and the radicado's existence was not even checked (the frontend's Keycloak client is public — PKCE — and MCP exposes free fields, so the BFF allowlist does not protect the backend). archive-service now **resolves everything server-side** (document-service `POST /internal/documentos/metadata` + storage) and **cross-checks** what was declared: unknown radicado → `404 radicado_no_encontrado`; a value different from the resolved one → `422 metadato_no_coincide` with `desacuerdos: {radicado_id: [fields]}` (names fields, never the resolved value — not an oracle for radicado numbers) and the audit entry `archive.radicado_vinculo_denegado`; document-service unreachable (or answering without `tracking_number`) → `503 document_service_no_disponible` — fail-closed for existence and number. What is persisted is ALWAYS the resolved value; only `folios` belongs to the declarant. Same cross-check in `…/radicados/batch`, all-or-nothing. **No-read-up**: resolution uses the caller's clearance; a radicado above their level gets the same `404 radicado_no_encontrado` as a missing one (no `desacuerdos`), and one without `doc_class` gets `422 radicado_sin_tipificar` (the typology is mandatory in the index and cannot be declared when linking). `POST /unidades/{id}/prestamos` returns `403 clearance_insuficiente` if the unit holds an expediente above the lender's clearance (gate in the `INSERT`'s `WHERE`). **Declared limit**: if a radicado is re-typed AFTER linking (`PATCH /documents/{id}` with `doc_class`), the already-generated index keeps the typology of link time; propagating it (event `document.radicado.retyped` + a "needs TRD review" mark) remains open.

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

A **pluggable** provider (`app/providers/`): `nativa` (HMAC hash+identity, the default for every object) and **`xades_local`** (real enveloped XAdES-B via `signxml`, active only for the **electronic index**). Selection: `xades_local` when `objeto_tipo=="indice"` **and** `SIGNER_PROVIDER=="xades_local"` (the default; kill switch `SIGNER_PROVIDER=nativa`); anything else → `nativa`. See [ADR-016 addendum Inc.1](adr/ADR-016-firma-electronica.md#addendum-incremento-1-2026-07-02-xades-b-del-indice-electronico-sello-institucional).

- `POST /api/v1/signature/sign-xml` `{payload_xml, objeto_tipo, objeto_id?, formato?, indice_id?, version?}` — signs the payload and persists the signature. With `objeto_tipo="indice"` it produces **XAdES-T** (XAdES-B with the tenant's institutional seal **+ an RFC 3161 timestamp** from a TSA, E06 Inc.4) and, if `SIGNER_LT_ENABLED=true` **and** the tenant's seal carries CA material (`ca.crt`+`crl.der`), it escalates to **XAdES-LTA** (E06 Inc.5): it adds `xades:CertificateValues` (the CA only) + `xades:RevocationValues` (the CRL) + `xadesv141:ArchiveTimeStamp`. With `SIGNER_OCSP_ENABLED=true` (default `false`) **and** the OCSP responder pair mounted (`ocsp.crt`+`ocsp.key`, per tenant alongside the seal), it additionally embeds a **stapled OCSP response (RFC 6960)** as `xades:OCSPValues/EncapsulatedOCSPValue`, a **sibling** of the CRL inside the same `RevocationValues` (E06 Inc.7) — signed by a **delegated responder** issued offline by the tenant's CA, with `certStatus` derived from the CRL, and covered by the `ArchiveTimeStamp`. Requires `PERM_FIRMA` with **`crud>=2`** + `X-User-Id` (the signer **never** comes from the body, RF-FIR-11). For chain types (`radicado|expediente|borrador`) `objeto_id` is mandatory and the object must exist and be visible by clearance (in the `WHERE`); otherwise a neutral `404 objeto_no_encontrado`. **The institutional seal (`objeto_tipo` ∈ `indice|acta_transferencia|acta_eliminacion`) additionally requires `X-Internal-Token` (AREA-04-07)**: archive-service sends it alongside the signed identity; a user holding only `PERM_FIRMA` gets `401/403` (before, they could stamp the entity's seal on an arbitrary XML). `objeto_tipo` is a **closed set** (`indice|acta_transferencia|acta_eliminacion|radicado|expediente|borrador`): anything else → `422`. Returns `{firma_id, payload_sha256, provider, minio_key_firmado?, xades_level?, nivel_conformidad?, tipo_firma?, certificado_fingerprint?, acreditado, tsa_timestamp?, lt_material_present?, archive_timestamp_present?, crl_next_update?, revocation_provenance?, ocsp_present?, ...}`. `xades_level` ∈ `XAdES-B|XAdES-T|XAdES-LT|XAdES-LTA` according to what was **achieved** (never what was intended; OCSP does **not** change the level, it enriches the revocation evidence); **without CA material the signature stays at XAdES-T** and leaves a `firma.lt_downgrade_no_ca` audit entry; **with OCSP enabled but no responder pair** it leaves `firma.ocsp_downgrade_no_responder` (staying at LT/LTA with the CRL alone). `acreditado` comes from what was **persisted at signing time** (`nivel_conformidad`, decided by the mounted seal: only a seal with `acreditacion.json` `{"acreditado": true}` on a read-only volume yields `xades_*_acreditado`); with the dev CA/TSA/OCSP responder it is `false` (not ONAC — validation material that is **verifiable, not accredited**, RF-FIR-15; `nivel_conformidad` ∈ `xades_b|xades_lt|xades_lta_no_acreditado`, dev revocation marked with `revocation_provenance="dev"`). `503 signing_seal_unavailable` if the seal is missing; **`503 timestamp_authority_unavailable`** if the TSA is unavailable; **`503 lt_material_required`** if `SIGNER_LT_REQUIRE=true` and CA material is missing; **`503 ocsp_material_required`** if `SIGNER_OCSP_REQUIRE=true` and no OCSP response was produced (missing CA or responder material); `503 signed_index_upload_failed` if storage rejects the upload. **`payload_sha256` is the SHA-256 of the BYTES that were signed, not of the document** (AREA-05-07): for `xades_personal` they are the `<acto xmlns="urn:orpyca:firma-personal" …>` envelope's, for a `nativa` signature of a draft it is the hash of the fingerprint's hexadecimal text, and for `radicado`/`expediente` the digest of `"{objeto_tipo}:{objeto_id}"`; **it is only comparable between signatures of the same `provider` and cannot be used to check against a PDF in hand** (the document's fingerprint is `firma_solicitud.payload_sha256`). Since migration `020` the `nativa` signature is produced with **`key_version=1`**: an HKDF-SHA256 key derived per **entity** (`signature_secret` stays the root secret, not rotated) and a message binding `tenant|objeto_tipo|objeto_id` (AREA-04-11) — a row copied into another schema stops verifying; earlier signatures (`key_version=0`) keep verifying under the original scheme.
- `POST /api/v1/signature/sign-personal` `{payload_xml, objeto_tipo, objeto_id?, totp_code, firmante_id?}` — **personal PKI signature of an arbitrary XML payload** with the authenticated actor's certificate (E17/F4 Inc.1; the `personal_signing.sign_personal_xml` primitive is reused by the radicado chain and by the transfer acta signature). Authenticated as a **user** (`get_actor` + `PERM_FIRMA`, `X-User-Id`/JWT — **not** `X-Internal-Token`: it carries the real signer's identity + TOTP, unlike `/internal/sign-indice`). **ASSERT `firmante_id == user(JWT)`**: if the body carries `firmante_id` and it differs from the actor derived from the token → `403 firmante_no_coincide` (not impersonable through the body; the persisted row always uses the JWT actor). It assembles the XAdES with an ephemeral RSA key + the real public certificate, asks auth-service to sign the `SignedInfo` digest **while consuming the TOTP atomically** (the remote-signing split), and uploads the signed XML to the object store under a deterministic name that includes the `firma_id` (the H-H pattern, no overwrite under a race). It returns the standard `FirmaResponse` (`firma_id`, `minio_key_firmado`, `xades_level`, `provider="xades_personal"`, `acreditado` **always `false`**, `nivel_conformidad='xades_personal_no_acreditado'`). **The no-read-up gate no longer hangs off `objeto_tipo`**: `objeto_tipo` is free text here (it has to be — institutional actas travel this route), and while the gate hung on `== "radicado"`, declaring any other value over a CLASSIFIED radicado's identifier evaluated it against level 1 and skipped it. The level is now resolved from the `objeto_id` against `radicados`/`expedientes`, whatever type is declared; if it does not resolve, it is an object with no level of its own (an acta, a draft) and stays at PUBLIC, and for those the no-read-up is still enforced by the **mediating service** (archive) **before** calling — verified that a direct call is not a bypass (it only signs the payload the caller itself supplies, it does not insert into the domain tables). **`403 identidad_incoherente`** if the forwarded bearer's owner (whose personal credential and TOTP these are) is not the `X-User-Id` actor the signature is attributed to (AREA-04-05): it is cut **before** any factor is requested and leaves a `firma.identidad_incoherente` entry. `4xx` if the TOTP is invalid; `503` if the auth channel is down.
- `POST /api/v1/signature/verify` `{firma_id, payload_xml}` — verifies. **`xades_local` signatures**: `payload_xml` must be the **complete signed XML** (the one at `minio_key_firmado`); it validates cryptography + root coverage + certificate validity + seal fingerprint + a match against `signed_xml_sha256`; **XAdES-T** additionally validates the RFC 3161 token (covering the `ds:SignatureValue`, CMS signature, TSA cert **pinned to `firma.tsa_cert_fingerprint`**); **XAdES-LT/LTA** additionally validates (fail-closed) the **`seal→CA` chain** (anchored to **the tenant's** CA via `ca_fingerprint` pinning — a seal from another tenant's CA fails), the CRL's **signature and non-revocation** (the seal's serial not revoked), and the **`ArchiveTimeStamp`** over the deterministic covered set; `crl_next_update` is exposed without a hard fail if it has lapsed (not accredited). If the signature carries **`xades:OCSPValues`** (E06 Inc.7), it additionally validates (fail-closed) the **OCSP response (RFC 6960)**: `responseStatus=successful`, responder issued by the tenant's CA + validity, `OCSPSigning` EKU + `id-pkix-ocsp-nocheck`, the `BasicOCSPResponse` signature, a **CertID bound to the seal** (serial + issuer hashes), `certStatus=good`, and **pinning** of the responder fingerprint against `firma.ocsp_responder_fingerprint` (without it → fail-closed); its absence is backward-compatible (LT valid on the CRL alone). **`xades_personal` signatures** (per-user personal PKI signature, E06/E17 Inc.1-2): it validates cryptography + coverage + validity + the leaf fingerprint; if the signature embeds the sub-CA (`xades:CertificateValues`, Inc.2) it additionally anchors the **leaf→tenant user sub-CA chain** with `user_ca_fingerprint` pinning (a branch separate from the seal's — it does not require an embedded CRL); and it **checks revocation online** (state in the DB, no cache) → `valida=false` (`motivo=certificado_revocado`) if the certificate was revoked. The verdict is broken out: `firma_criptograficamente_valida` (cryptography+chain alone) and `revocacion_status ∈ {vigente, revocado, no_verificable, desconocido}`; `valida = cryptography AND revocacion_status=='vigente'` **except for a revocation AFTER the signature** (AREA-04-13): if the certificate was revoked **after** the signature's instant (ONLY a verified TSA's `genTime`: the server's `sello_tiempo` is not a trusted date) the signature stays valid (`valida=true`, `revocacion_status="revocado"`) and the response carries **`revocacion_posterior=true`**; revoked **before** (or with no readable `revocado_at`) → `valida=false`. If the revocation reason is `key_compromise` it invalidates retroactively (`valida=false`, `motivo=clave_comprometida`); with no verified TSA there is no trusted instant: `valida=false`, `motivo=instante_firma_no_cierto`, `revocacion_posterior=null`; a downed revocation channel → `valida=false`/`motivo=revocacion_no_verificable` but `firma_criptograficamente_valida=true` (fail-closed by default, `verify_personal_revocation_require`). Inc.1 `xades_personal` signatures (without `CertificateValues`) verify as XAdES-B/T (grandfathered) but **remain revocable** by certificate identity. **`nativa` signatures**: `payload_xml` is the original plaintext; the HMAC is recomputed under the scheme of the row's `key_version`, and with the request's entity. **Timestamp (XAdES-T, AREA-04-08)**: `genTime` must fall within the **signing certificate's** validity (internal `motivo` `gentime_fuera_de_vigencia_firmante` → `sello_tiempo_valido=false`) and, if the token carries the `signing-certificate-v2` attribute (ESSCertID, RFC 5035), its hash must match the TSA certificate; its absence is rejected (`tsa_sin_esscertid`) when the stamp's `genTime` is **on or after the cut-off date** `TSA_ESSCERTID_CORTE` (ISO 8601, no zone = UTC; **global** — the TSA is deployment infrastructure; default = this change's deployment date, `2026-10-04T00:00:00Z`); **before the cut-off** the stamp stays valid and the response declares it in `advertencias` (`tsa_sin_esscertid_anterior_al_corte`: tokens from the local TSA prior to AREA-04-08 do not carry it and are embedded, immutable, in signatures already issued). Replaces the `TSA_ESSCERTID_OBLIGATORIO` boolean. **`acreditado`** is derived from what was persisted at signing time (`nivel_conformidad` ∈ `xades_{b,lt,lta}_acreditado`) and **only if the cryptography validates** (AREA-05-02); it used to be a literal `false`. Returns `{firma_id, valida, provider, xades_level?, nivel_conformidad?, acreditado, motivo?, sello_tiempo_valido?, tsa_timestamp?, lt_material_present?, archive_timestamp_present?, crl_next_update?, revocation_provenance?, ocsp_present?, ocsp_cert_status?, firma_criptograficamente_valida?, revocacion_status?, revocacion_posterior?, advertencias[]}` (`advertencias: list[str]` is **additive** and services consuming `/verify` must keep it: archive-service propagates it in its `VerifyResult`) (in `/verify` the **authoritative** field for the level actually verified is `xades_level`, derived from the result). **Not public** (AREA-04-10): through the gateway it requires a JWT and the tenant comes from the token; truly public verification goes through `/api/v1/public/` with a verification code (E13). Inside the entity it applies **no read-up**: the signature of a radicado/expediente above the caller's clearance gets the **same `404 firma_not_found`** as a non-existent `firma_id` (the level is inside the query's `WHERE`); likewise on `GET /api/v1/signature/firmas/{firma_id}`. Service-to-service calls with no `X-User-Id` (archive-service verifying an index's seal) have no clearance to apply. `404` if the signature does not exist.
- `GET /api/v1/signature/firmas/{firma_id}` — a signature's metadata (the same fields as `sign-xml`, without the full `certificado_pem`). Public. `404` if it does not exist.
- _(Internal, service-to-service; **not** reachable from the gateway — `X-Internal-Token` is not in `FORWARDED_REQUEST_HEADERS`)_ `POST /api/v1/signature/internal/sign-indice` `{payload_xml, objeto_id, version, objeto_tipo?, indice_id?}` — seals a XAdES-eligible object **attributed to the system principal `SYSTEM_RECONCILER_ID`** (not to a human), for archive-service's **reconciliation jobs** (the E15/E06 Inc.8 index, and the H-H transfer acta). Authenticated **only** by `X-Internal-Token` (fail-closed: empty setting → 503, missing header → 401, wrong header → 403); it requires neither `PERM_FIRMA` nor `X-User-Id`. `objeto_tipo` (default `"indice"`) is **validated against the allow-set `{"indice","acta_transferencia"}`** (`422` outside it) → only those two types produce the tenant's institutional seal (which does NOT derive from the actor); **structurally it cannot sign the personal chain**. The `firma.creada` entry carries `origen="reconciliation"`. The personal signature (`/requests/{id}/sign`, `/sign-xml`) NEVER accepts this token.


**Signature chain (the signer's inbox, RF-FIR-04).** All of these require `PERM_FIRMA`; the signer is derived from the JWT (never from the body, RF-FIR-11). The active turn is the lowest `orden` still pending. A personal signature requires **2FA** (RF-FIR-13): either an **email OTP** (E06 Inc.2) or, stronger, **TOTP RFC 6238** (device possession, E06 Inc.6, see [ADR-016 addendum Inc.6](adr/ADR-016-firma-electronica.md#addendum-incremento-6-2026-07-06-2fa-por-totp-rfc-6238-en-la-firma-personal)). The TOTP secret lives in **auth-service** (AESGCM-encrypted); see `POST /api/v1/auth/me/totp/*` above. **Personal signature format (E06/E17 F4, Inc.1)**: if the signer has an **active PKI signing key** (see `/me/signing-key/*`), the chain produces a per-user **XAdES-B/T** signature with their own certificate (third-party verifiable via `/verify`, `provider=xades_personal`); otherwise it degrades to the **native HMAC signature** (`provider=nativa`, backward compatible). Both are **art. 7 non-accredited** electronic signatures (`acreditado=false`) — the per-user PKI adds verifiability, format and cryptographic separation, not reinforced legal value (server custody ⇒ no sole control). **XAdES gate by level (Inc.3; an **entity-level** policy: parameter `firma_personal_require_xades` ∈ `off`|`clasificados`|`todos`, default `off`, editable with `PUT /config/params/firma_personal_require_xades`; the `FIRMA_PERSONAL_REQUIRE_XADES` env only backs up an entity that does not define it, and an unknown value in the table is applied as `todos`)**: with `clasificados`, an act at `nivel_seguridad>=2` **no longer degrades** to native — no active certificate → **`422 firma_xades_requerida`** (with enrollment guidance), auth-service down → **`503 canal_firma_personal_no_disponible`** (fail-closed); with `todos`, it applies at any level. A denial leaves an atomic `firma.xades_requerida_denegada` entry and **consumes** no 2FA factor. In a batch it is evaluated **per item**. The legacy boolean env var is accepted (`false`→`off`, `true`→`todos`); an unrecognised value aborts startup. Only enable `clasificados` after completing enrollment for signers with clearance>=2 (certificate issuance is offline).

- `POST /api/v1/signature/requests` `{objeto_tipo, objeto_id, titulo?, payload_sha256?, firmantes:[uuid,…], nivel_seguridad?}` — creates the chain (turns `orden` 1..N). `409 cadena_existente` if the object already has an open chain. _Note:_ to sign with 2FA the request must carry a stable `payload_sha256`. **The content is signed, not the identifier (AREA-04-09):** for `radicado` and `expediente` the body's `payload_sha256` is **ignored** — the SERVER resolves the fingerprint when the chain opens (SHA-256 of the radicado's **main document** (`es_principal` annex) from document-service, or of the expediente's **current electronic index** from archive-service, both via an internal endpoint with `X-Internal-Token`) and that is what gets signed. It is re-read before **every** signature and approval: if it changed, `409 documento_modificado` (open a new chain). Fail-closed: producer down → `503 verificacion_documento_no_disponible`; nothing to seal → `409 radicado_sin_documento` / `indice_no_disponible` / `anexo_sin_huella`; missing object → the neutral `404`. **Chains opened before this change** (`NULL` fingerprint) are not signed blind: `409 cadena_sin_huella`; while they stay `pendiente` they hold the open-chain index, so the operator invalidates them in bulk with `python -m app.ops.invalidar_cadenas_sin_huella [--dry-run] [--tenant slug]` (state `invalidada`, `invalidada_motivo='cadena_sin_huella'`, one `firma.cadena_invalidada` entry per chain in the same transaction; idempotent) and the replacement chain can then be opened. The `503 verificacion_documento_no_disponible` names only the producer (`document-service`/`archive-service`) and the HTTP code: it never forwards the body the producer answered. The `firma.solicitud_creada` and `firma.turno_firmado` entries carry `huella_documento_sha256` (the one fixed when the chain opened) and `firma.turno_firmado` also `gate_modo` (the effective XAdES policy). And where the chain fixes no fingerprint, **`/sign` rejects `payload_xml` with `422 payload_xml_no_admitido`**: the signer does not choose the bytes at signing time.
  - **`objeto_tipo` is bounded** to `radicado` · `expediente` · `borrador` · `documento` (`422` outside the set). This is not cosmetic validation: the chain's security level used to be resolved by comparing this field —free text from the body— against the literal `"radicado"`, so `"Radicado"` was enough to bypass the authoritative source (RF-SEG-08).
  - **The level is decided by the object, not by the body.** If `objeto_id` resolves against `radicados` or `expedientes`, that row's real `nivel_seguridad` prevails and the body's is ignored — it is resolved **without looking at `objeto_tipo`**, so declaring `borrador` over a CLASSIFIED radicado's identifier lowers nothing either. `nivel_seguridad` only takes effect for unregistered objects (draft/document), where there is no table to consult.
  - **Fail-closed**: `objeto_tipo` ∈ {`radicado`, `expediente`} whose `objeto_id` does not resolve → `404 solicitud_not_found`, the same neutral answer as no-read-up (it does not distinguish "does not exist" from "you cannot reach it"). Previously it fell back to the level declared in the body.
- `GET /api/v1/signature/requests/{id}` — the chain's state: header + items `{orden, firmante_id, estado, firma_id, comentario, decidido_at}`. **Accepted scope (product decision, not a defect):** anyone holding `PERM_FIRMA` whose clearance reaches the object (no-read-up) can read it, signer or not, so an area head or an auditor can follow a procedure. It exposes state and turns, not content. Restricting it to participants would be a separate product decision.
- `GET /api/v1/signature/requests?objeto_tipo=&objeto_id=` — an object's open chain (`404` if there is none). Same accepted scope as the read by id (`PERM_FIRMA` + clearance).
- `GET /api/v1/signature/pending?page=&size=` — the inbox: requests whose **active turn** belongs to the authenticated user (`X-Total-Count`).
- `POST /api/v1/signature/requests/{id}/challenge` — **(RF-FIR-13)** issues the 2FA challenge: it generates a 6-digit OTP bound to `(request, active-turn signer, document hash)`, emails it to the signer and returns `{challenge_id, factor_tipo:"otp_email", canal:"j***@dominio", expira_at}` (TTL 5 min). Only the active-turn signer (`403 no_es_su_turno`); `404 solicitud_no_encontrada` (neutral); **`422 payload_no_fijado`** if the request has no `payload_sha256`; `429 challenge_cooldown` if reissued within 30s; `503 canal_2fa_no_disponible` if the email cannot be sent (fail-closed). The OTP **never** appears in the response or in logs. **`403 identidad_incoherente`** if the bearer's owner (whose mailbox the code would go to) is not the actor the signature is attributed to (AREA-04-05): no challenge is created and no email leaves, and a `firma.identidad_incoherente` entry is left. **`409 totp_requerido`** if the act is `nivel_seguridad ≥ 2` and the actor **already has an active TOTP** (AREA-04-02): the email does not lower the factor; fail-closed (`503 canal_2fa_no_disponible` if it cannot be queried). The emailed OTP is stored as an HMAC under a **per-entity key** that binds the `challenge_id` (`key_version=1`, AREA-04-11).
- `POST /api/v1/signature/requests/{id}/sign` `{payload_xml?, challenge_id?, otp?, totp_code?}` — signs the active turn (reusing the native HMAC signer) requiring **verified 2FA**; it advances the chain and, on signing the last turn, the request moves to `firmado`. **Factor selection**: if `totp_code` (6 digits) arrives and the signer has TOTP **active** → it is verified against auth-service (**no** prior `/challenge` needed); if `challenge_id`+`otp` arrive → email OTP; if `totp_code` arrives but the signer does **not** have active TOTP → **`422 totp_no_activo`** (it does not silently fall back to email); both → TOTP prevails; neither → **`409 2fa_requerido`**. TOTP errors: `401 totp_incorrecto` `{intentos_restantes}`, `423 totp_bloqueado`, `503 canal_2fa_no_disponible` if auth does not answer (fail-closed → rollback). Email-OTP errors: `401 otp_incorrecto` `{intentos_restantes}` (5 attempts → `challenge_bloqueado`), `409 challenge_expirado`/`challenge_consumido`/`challenge_documento_distinto`/`challenge_acto_distinto`, `403 challenge_ajeno`. Common: `403 no_es_firmante` and `403 no_es_su_turno`; `409` if the request is not pending or the turn race is lost (`FOR UPDATE` lock). **Guard order (AREA-04-09)**: neutral 404 (no-read-up) → `403 no_es_firmante` → `409 solicitud_no_pendiente`: someone outside the chain gets the same 403 whatever the state and cannot probe whether it is open, signed or rejected (same in `/reject`, `/vobo`, `/challenge` and per item in `/batch`). 2FA is **non-disableable** for `nivel_seguridad ≥ 2` (RESERVED/CLASSIFIED) — both factors satisfy it, **unless the actor already has an active TOTP: then the email does not count and the answer is `409 totp_requerido`** (AREA-04-02; before, controlling the mailbox was enough to skip the authenticator). The verified TOTP, the personal credential and the email must belong to the **actor** the signature is attributed to: if the bearer's owner is someone else → `403 identidad_incoherente` with a `firma.identidad_incoherente` entry and without marking the turn (AREA-04-05). With the entity policy at `off`, if the credentials channel (`/internal/signing-key/status`) is down and the signature falls back to `nativa`, a **`firma.fallback_nativa_canal_caido`** entry is left in the same transaction as the signature (AREA-04-12; enabling the `clasificados` gate is the entity's decision and requires enrolling certificates first). The personal credential's state is queried **before** the turn's row lock (AREA-04-06).
- `POST /api/v1/signature/requests/{id}/vobo` `{comentario?}` — resolves a turn with `rol='visto_bueno'`. It produces no `firma` and does not generate the final document. The comment is **optional**, unlike a rejection's reason: approving needs no explanation, halting somebody else's procedure does. `409 turno_es_de_firma` on a signature turn — resolving one through here would be the way to commit the institution without signing.
- **Two-factor is not required for a visto bueno** (D-6): what is always mandatory is the **anchoring to the digest**, which is what turns "approved" into "approved *this* text". Demanding a signing credential and 2FA from every reviewer is workable in a three-person institution and not in a forty-person one; the digest plus `audit_log` already prove what each of them saw. The final signer **does** sign with PKI: they are the one committing the institution.
- `POST /api/v1/signature/requests/{id}/reject` `{motivo}` — rejects the active turn and **stops** the chain (request `rechazado`).
- `POST /api/v1/signature/batch` `{items:[{solicitud_id, challenge_id?, otp?, totp_code?}, …]}` — batch-signs the user's turns, with **one second factor per document** (RF-FIR-13): email OTP (`challenge_id`+`otp`) or `totp_code`; it does not abort on an individual failure (`{resultados:[{solicitud_id, resultado:'firmado'|'omitido', detalle?}]}`). _(Contract changed in E06 Inc.2: previously `{solicitud_ids:[…]}`.)_

### Postal shipments (E20) — document-service

The lifecycle of the physical dispatch of an outbound radicado. State machine `registrado → en_transito → entregado/devuelto/fallido` (`entregado`/`devuelto`/`fallido` are terminal). The human-facing routes require **`PERM_RADI_SALIDA`** and apply clearance-based no read-up.

- `POST /api/v1/documents/{document_id}/envios` `{operador, destinatario?, direccion?}` — dispatches the radicado with a carrier (auto-generated tracking number). `201`. It resolves the caller's clearance → `404 radicado_not_found` if the radicado does not exist **or** exceeds it (indistinguishable): what cannot be seen is not dispatched. **Since T4:** requires `PERM_RADI_SALIDA` **level 3**; writes `document.envio_registrado` (actor = `X-User-Id`) in the same transaction; the waybill derives from a per-shipment id, so several shipments of the same radicado and operator are possible.
- `GET /api/v1/documents/{document_id}/envios` — the radicado's shipments (clearance-filtered).
- `GET /api/v1/envios?estado=&page=&size=` — tenant-wide listing (`X-Total-Count`, no read-up).
- `GET /api/v1/envios/{shipment_id}` — detail (`404` if the radicado exceeds the caller's clearance).
- `PUT /api/v1/envios/operadores/{operador}/credencial` `{secreto (≥32), gracia_horas = 24 (0-168)}` — (T13 / AREA-09-10) **rotates the operator's webhook HMAC secret** (`4-72|servientrega|otro`). Gate `USUA_PERM_ADMIN` **crud≥5**. Two slots: the new secret becomes current and the previous one stays valid until `anterior_caduca_at` (`gracia_horas`; 0 = hard cut): the callback accepts **either** valid slot (`verify_any`, no short-circuit) and expiry is decided in Python with the service clock. Secrets are stored **encrypted** (AESGCM, AAD = entity + operator, same master key `EMAIL_ENCRYPTION_KEY`; without it `503 custodia_no_configurada`: nothing is stored in clear); the secret is **never** returned or audited (entry `document.postal_credencial_rotada` with `gracia_horas`/`primera_vez`). Response `{operador, rotada_at, anterior_vigente_hasta}`. Migration `044`; the legacy clear-text column is emptied on first rotation.
- `POST /api/v1/envios/{shipment_id}/estado` `{estado, descripcion?, motivo?, acuse_file_id?, acuse_sha256?}` — **a manual `entregado` requires the delivery receipt** (T13 / AREA-09-10): `acuse_file_id` (the storage `file_id`) **and** `acuse_sha256` (64 hex) → `422` if missing; the server **checks them against storage** (B2, ADR-031): the file must exist in the tenant and the hash must match (`422 acuse_invalido`, only if the file is theirs), **it must have been uploaded by whoever records the delivery** (`403 acuse_ajeno`, also when the file has no author **or does not exist**: answered the same so as not to reveal which `file_id` exist; checked before the hash) and **it must not already be something else** — a radicado attachment, a draft, an email, another shipment's receipt… (`409 acuse_ya_referenciado`; only a retry of the SAME shipment with its own receipt is idempotent); `503 acuse_no_verificable` if storage is down. Storage deduplicates per author, so re-uploading the same PDF gives the same `file_id` and lands on 409: it is the same evidence. A partial unique index on `postal_shipment(acuse_file_id)` (migration `045`) closes the race between two simultaneous recordings (→ `409 acuse_ya_referenciado`) and the hash returned by storage is the one recorded; persisted in `postal_shipment.acuse_*`, returned in the response and carried in the `document.envio_estado` audit entry. The operator's signed callback does not need them (its signature is its proof). A **manual** transition (human dispatch). `motivo` feeds `motivo_devolucion` —the cause in its own queryable field— as the carrier's inbound webhook already did; without it the cause only lived inside the free text of `descripcion`. It resolves the caller's clearance → `404` if the radicado exceeds it (closing D-14); `409 transicion_invalida` for a disallowed jump. **Since T4:** requires `PERM_RADI_SALIDA` **level 3**, writes `document.envio_estado` in the same transaction, and the `UPDATE` compares previous state and clearance: if the shipment changed meanwhile it answers `409 transicion_invalida`.
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

- `POST /api/v1/export` `{comentario?, year?, doc_type?}` — **exports radicados to an interoperable package** (ZIP). **Authenticated** (not public); gate `USUA_PERM_EXPEDIENTE` + **per-radicado no read-up** (only what the caller can read is exported; those above clearance are omitted and reported content-free in the manifest). `application/zip` response with the headers `X-Export-UUID`, `X-Export-Count`, `X-Export-Excluded`. ZIP contents: `manifiesto.json` (export UUID, comment, start/end marks, list of radicados with SHA-256, exclusions), `esquema/radicado.schema.json` (the published **JSON Schema draft 2020-12**), `radicados/{numero}.json` (per-radicado data that **validates against the schema**: system + contextual metadata, disposition, classification, attachments, history), `anexos/{numero}/{i}-{archivo}` (binaries), `checksums.txt` (SHA-256 per member). The **radicado number is preserved** as identity (AGN Agreement 060/2001). Physical ZIP packaging is provisional (BagIt/OAIS+WORM = E10). Optional filters `year` (2000–2100) and `doc_type` (E/S/I). **Since T4:** gate `USUA_PERM_EXPEDIENTE` **level 3**; one import/export at a time per process (`429 operacion_ocupada` with `Retry-After`); 512 MiB cap on attachments (`422 export_demasiado_grande`).

- **Location in the package (ADR-029).** `metadata_sistema.ubicacion_dept` / `ubicacion_dept_code` are **informational** (where the radicado was at export time). `POST /api/v1/import` **ignores** them: the location of what is imported is derived by the receiving entity's own workflow. The holder (`flujo_usuario`) never leaves the system in a package.
- `POST /api/v1/import` — **imports an interoperable package** of radicados. **Authenticated**; gate `USUA_PERM_EXPEDIENTE` + **clearance-based no write-up**. It receives the INT-01 ZIP as a **raw body** (`Content-Type: application/zip`, cap 512 MiB). **ALL-OR-NOTHING validation before ingesting**: it validates every radicado against the service's trusted JSON Schema and **verifies the SHA-256 of every binary**; if anything fails → `422 paquete_invalido` **leaving no partial state**. It re-ingests **preserving the radicado number** (a collision is **skipped**, never overwritten); it rebuilds the E↔S relationship (`responde_a`) by number; it uploads the binaries to storage; it audits `radicado.import`. Anti zip-slip/zip-bomb guards. Response `{import_uuid, importados, omitidos_existentes, omitidos_clearance, relaciones_reconstruidas}`. A radicado whose `nivel_seguridad` exceeds the importer's clearance is **skipped** (`omitidos_clearance`). **ADR-028:** `metadata_sistema.status` only admits `registered|distributed|in_progress|closed`: a package carrying `anulado` (or any other value) is rejected whole with `422` —it brings no annulment act to justify it—; the `radicado.import` audit entry records each imported radicado's `status_origen`. **Since T4:** gate `USUA_PERM_EXPEDIENTE` **level 3**; `429 operacion_ocupada` while another import/export runs; `413` as soon as the body exceeds 512 MiB (without buffering it whole); the sequence counter only advances for THIS entity's numbers, and a sequence > 999999 or a year outside [1900, current] is rejected with `422 paquete_invalido`. **T4 review:** a number carrying THIS entity's code with a sequence > 999998 is also `422` (the next native one would have 7 digits); the body must arrive within 300 s or it answers `408 cuerpo_demasiado_lento` (a trickling client no longer holds the slot shared by every tenant). **Orphan binaries (AREA-09-14):** binaries uploaded for radicados that never came to exist (full rollback or a skip by race) are recorded as `document.import_binarios_sin_radicado` (`file_id` + `sha256`, content-free) and are now also **purged**, not just recorded. They are queued in `import_binario_huerfano` (migration 038, in the same transaction as the audit entry) and document-service asks storage `POST /storage/internal/purgar-huerfanos` (`X-Internal-Token`): storage deletes an object only if that import created it (`uploaded_at >= subido_desde`), NO attachment/email/draft/migration/WORM object in the tenant references it (storage deduplicates by SHA-256), NO `upload` returned it through deduplication (`files.dedup_hit_at`, storage migration 017: the mark is written by an `UPDATE` that takes the row lock, so it serialises with the purge's `FOR UPDATE` —without it the purge only saw committed references and could delete an object another operation had just received—; if the purge wins, the upload stores a new object instead of returning a dangling `file_id`), a missing references table in the tenant is treated as "unknown" (nothing is deleted) and the object store accepts the deletion, all in its transaction, with the `file.purgado_huerfano` audit entry; a confirmed purge writes `document.import_binarios_purgados`. If storage does not answer or a file returns `error`, the row stays `pendiente` and the sweep retries (up to 20 times → `agotado`, manual review, and that step writes `document.import_binario_agotado` in the same transaction); the `…_sin_radicado` entry is already written. Declared debt: the own-numbering window (there is no jump cap over `last_seq`, so as not to prevent restoring an empty tenant).

### MCP layer (E18, ADR-019) — mcp-server, port 8009

It exposes the API as _tools_ to agents/LLMs, while itself being a client of the gateway. The **MCP protocol** is available over **stdio** (`python -m app.mcp_stdio`) and **streamable HTTP**.

**It is now routed through the gateway** (`/api/v1/mcp/` → mcp-server), which it was not before: catalogue and protocol were left outside, so an external MCP client had no way in except talking to the service behind the gateway's back, with no JWT validation and no `X-Tenant-Slug`. Exposing it is safe because the write gate no longer depends on the client (signed proposal, see above). The streamable transport is mounted at `POST /api/v1/mcp/stream` in addition to `/mcp`.

The session context is **two headers**: `Authorization` and `X-Tenant-Slug`. `X-User-Permissions` and `X-User-Roles` are **no longer read** — nobody produced the former and the gateway drops it as forgeable, so it always arrived empty and every permissioned tool was invisible over that transport; permissions come from `GET /api/v1/auth/me` with the caller's token, and with them the **CRUD level**. The equivalent environment settings (`MCP_USER_PERMISSIONS`, `MCP_USER_ROLES`) have been removed.

**Scope framework**: the `mi_alcance` tool and the `orfeo://alcance` resource return what the person can execute, what they can read, and what they **cannot** with its reason and its **alternative route**. It is always offered, even to someone who reaches no write at all.

Simple HTTP catalog API:

- `GET /api/v1/mcp/tools` — catalogue of the tools THIS caller can use, resolved against `/auth/me` (permission **and** level). Includes the **procedures** (`method: "PROCEDIMIENTO"`), listed alongside the tools rather than in a separate catalogue: to whoever consumes this, "register a document" and "handle a citizen petition" are two things they can ask for.

#### Procedures: the whole matter, not the isolated call

A tool maps to **one** endpoint. That is enough to query and falls short of what a civil servant does: registering a petition is four acts —register, route, file, notify—. With isolated tools the model chains them: it picks the order, invents the link between one step and the next, and when something fails halfway nobody knows what state the matter is in.

A procedure declares that sequence **server-side**, named and in a fixed order, and stays declarative (a table entry, not new code per procedure). Available today: `tramitar_peticion_ciudadana`, `responder_radicado`, `conformar_expediente` and `estado_del_tramite` (read-only; composes five queries into one answer).

Three properties, and all three matter:

1. **Every step travels with the person's token.** A procedure is an ordering, not a credential: if the user cannot assign, that step returns `403` exactly as if they had called the tool by hand. It can do nothing its user cannot.
2. **One confirmation for the whole act.** A write procedure returns a signed proposal with its chain of steps visible — whoever confirms has a right to know what they are approving. Asking for one confirmation per step would teach people to confirm without reading.
3. **Atomicity is not faked.** No transaction spans HTTP calls to different services, and **registering consumes an immutable number that never returns to the counter**. So the result is a step-by-step report (`pasos`, `pasos_ejecutados`, `completado`, `advertencia`) rather than a boolean: the caller needs to know they hold a numbered, unrouted radicado — not a bare "it failed" that would lead them to register it again.

Declared coverage: the gateway census is **405 operations**, of which **63 are sealed system acts** (reconciliation, service-to-service internal routes) that no human channel exposes. Of the remaining **342 the MCP catalogue covers 203 — 59 %**. The per-workflow breakdown, with what is missing in each, is in [API by workflow](mcp-flujos.md) (Spanish only); it is said here instead of being discovered by asking for something that does not exist. **The census is derived from the code at every review, never copied**: this page's earlier figure (367 operations, 41 tools) fell eight months behind the catalogue while the changelog did record every increment — a hand-written number next to one that moves.
- `POST /api/v1/mcp/tools/{name}/invoke` `{...args}` — invokes the tool: propagates `Authorization` + `X-Tenant-Slug` to the gateway. Identity and permissions are resolved against `GET /api/v1/auth/me` using the caller's token (permission **and** CRUD level; `403` if insufficient). The full catalogue — **207 tools** — is in the table below, grouped by epic.
  **Write operations are NOT executed here: they return a proposal.** `X-Confirm-Write` is **no longer read** — it was a boolean any client could set, and it bound the confirmation to no particular act (it confirmed "something", not "this"). The response is `{kind:"proposal", ejecutado:false, proposal:{id, tool, operacion, campos, expira_en, token}, aviso}`: `campos` are the human-readable fields of the act — worded by the **catalog**, never by the model — and `token` is an opaque HMAC-SHA256-signed value carrying the tool and its already-validated arguments. It expires after 120 s and is bound to the user and tenant that requested it.
- `POST /api/v1/mcp/proposals/execute` `{token}` — executes the act signed inside the token. **The body carries only the token**: the arguments come from it, so what runs is exactly what was described. It **re-authorizes** on confirmation (the permission may have been revoked between proposing and confirming: a proposal attests _what_ was approved, not that whoever presents it may still do it) and **consumes** the proposal before dispatching. Errors: `400 propuesta_invalida`, `403 propuesta_ajena` / `permiso_insuficiente`, `409 propuesta_consumida`, `410 propuesta_expirada`.
  The same act over the **MCP protocol**: writes return the proposal as structured content and are confirmed with the `confirmar_accion(token)` tool; where the transport has a return channel (**stdio**) the user is asked in-band via **elicitation**, using those same fields. Over streamable HTTP (`stateless`) there is no session to ask and only the two-tool path remains — a declared limit. Tools carry MCP `annotations` (`readOnlyHint`, `destructiveHint`) as **hints**, so the client can add its own friction: the guarantee still lives in the signed proposal, not in them.
  _Declared limit_: the consumption registry lives in process memory — it covers double-clicks and retries, not multi-replica. `Idempotency-Key: <jti>` is emitted to the gateway as a hook for each epic to implement, not as a guarantee.

The current catalogue: **207 tools, 75 of them writes** — **27 declared irreversible** and **6 requiring a value typed from the card itself** (not a fixed phrase: reproducing the batch identifier confirms *this* act and cannot be typed from memory in advance). Every write returns a signed proposal and requires explicit confirmation (see above). The table is **generated from `services/mcp-server/app/tools/catalog.py`**: each row's permission and CRUD level are the ones the catalogue declares, and `shared/tests/test_permisos_declarados.py` checks in both directions that they match what the endpoint demands.

**W** = write · **R** = read · a bold CRUD level (**3**) is create/delete. The **Operation** column reproduces the label the server itself returns in the confirmation card, which is written in Spanish and is not translated here: it is system data, not prose, and a translated copy would be a second wording no one would keep in step with the catalogue.

**E01 · Registration and correspondence** — 27 tools, 11 writes

| Tool | Operation (verbatim, as the server shows it) | Endpoint | Permission (crud) | |
| --- | --- | --- | --- | --- |
| `actualizar_radicado` | Corregir los datos de un radicado | `PATCH /documents/{document_id}` | `PERM_RADI` (1) | **W** |
| `aprobar_anulacion` | Aprobar la anulación de un radicado | `POST /documents/{document_id}/anulacion/aprobar` | `PERM_PANU_CODI` (1) | **W** irreversible |
| `aprobar_borrador` | Aprobar un borrador de salida | `POST /borradores/{borrador_id}/aprobar` | `PERM_RADI` (1) | **W** |
| `crear_borrador` | Preparar un borrador de salida | `POST /borradores` | `PERM_RADI` (1) | **W** |
| `crear_respuesta_vinculada` | Responder un radicado con una Salida vinculada | `POST /documents/{document_id}/respuesta` | `PERM_RESPUESTA_VINCULADA` (1) | **W** irreversible |
| `editar_borrador` | Corregir un borrador de salida | `PATCH /borradores/{borrador_id}` | `PERM_RADI` (1) | **W** |
| `radicar_borrador` | Radicar un borrador (le asigna número definitivo) | `POST /borradores/{borrador_id}/radicar` | `PERM_RADI` (1) | **W** irreversible |
| `radicar_documento` | Radicar un documento de entrada, salida o interno | `POST /documents` | `PERM_RADI` (1) | **W** irreversible |
| `rechazar_anulacion` | Rechazar la anulación de un radicado | `POST /documents/{document_id}/anulacion/rechazar` | `PERM_PANU_CODI` (1) | **W** |
| `registrar_tercero` | Registrar un tercero en el catálogo | `POST /terceros` | `PERM_RADI` (1) | **W** |
| `solicitar_anulacion` | Solicitar la anulación de un radicado | `POST /documents/{document_id}/anulacion` | `PERM_SOL_ANULAR` (1) | **W** |
| `buscar_terceros` | Buscar un tercero en el catálogo | `GET /terceros` | `PERM_RADI` (1) | R |
| `cola_anulaciones` | Consultar la cola de anulaciones pendientes | `GET /anulaciones` | `PERM_PANU_CODI` (1) | R |
| `consultar_actores` | Consultar los actores de un radicado | `GET /terceros/radicado/{radicado_id}/actores` | `PERM_RADI` (1) | R |
| `consultar_anulacion` | Consultar la anulación de un radicado | `GET /documents/{document_id}/anulacion` | _no permission_ | R |
| `consultar_borrador` | Consultar un borrador | `GET /borradores/{borrador_id}` | `PERM_RADI` (1) | R |
| `consultar_por_numero` | Consultar un radicado por su número | `GET /documents/by-tracking/{tracking_number}` | _no permission_ | R |
| `consultar_radicado` | Consultar un radicado | `GET /documents/{document_id}` | _no permission_ | R |
| `historial_acceso_radicado` | Ver quién ha consultado un radicado | `GET /documents/{document_id}/historial-acceso` | _no permission_ | R |
| `listar_borradores` | Ver los borradores de salida | `GET /borradores` | `PERM_RADI` (1) | R |
| `listar_departamentos` | Consultar los departamentos DIVIPOLA | `GET /terceros/divipola/departamentos` | `PERM_RADI` (1) | R |
| `listar_municipios` | Consultar los municipios de un departamento | `GET /terceros/divipola/municipios` | `PERM_RADI` (1) | R |
| `listar_plantillas` | Consultar las plantillas de redacción | `GET /plantillas` | `PERM_RADI` (1) | R |
| `listar_radicados` | Listar radicados | `GET /documents` | _no permission_ | R |
| `listar_respuestas` | Ver qué respuestas se han dado a un radicado | `GET /documents/{document_id}/respuestas` | _no permission_ | R |
| `radicados_de_un_tercero` | Ver en qué radicados aparece un contacto | `GET /terceros/{tercero_id}/radicados` | `PERM_RADI` (1) | R |
| `terceros_duplicados` | Ver posibles contactos duplicados | `GET /terceros/duplicados` | `PERM_RADI` (1) | R |

**E02 · Case files** — 9 tools, 5 writes

| Tool | Operation (verbatim, as the server shows it) | Endpoint | Permission (crud) | |
| --- | --- | --- | --- | --- |
| `actualizar_expediente` | Corregir los datos de un expediente | `PATCH /expedientes/{expediente_id}` | `USUA_PERM_EXPEDIENTE` (2) | **W** |
| `cerrar_expediente` | Cerrar un expediente (irreversible) | `POST /expedientes/{expediente_id}/close` | `USUA_PERM_EXPEDIENTE` (**3**) | **W** irreversible |
| `crear_expediente` | Abrir un expediente | `POST /expedientes` | `USUA_PERM_EXPEDIENTE` (**3**) | **W** |
| `excluir_radicado` | Excluir un radicado de un expediente | `DELETE /expedientes/{expediente_id}/radicados/{radicado_id}` | `USUA_PERM_EXPEDIENTE` (**3**) | **W** irreversible |
| `vincular_radicado` | Vincular un radicado a un expediente | `POST /expedientes/{expediente_id}/radicados` | `USUA_PERM_EXPEDIENTE` (2) | **W** |
| `consultar_expediente` | Consultar un expediente | `GET /expedientes/{expediente_id}` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `consultar_foliado` | Consultar la foliación de un expediente | `GET /expedientes/{expediente_id}/foliado` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `hoja_ruta_expediente` | Consultar la hoja de ruta de un expediente | `GET /expedientes/{expediente_id}/eventos` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `listar_expedientes` | Listar expedientes | `GET /expedientes` | `USUA_PERM_EXPEDIENTE` (1) | R |

**E03 · Metadata** — 3 tools, 1 writes

| Tool | Operation (verbatim, as the server shows it) | Endpoint | Permission (crud) | |
| --- | --- | --- | --- | --- |
| `tipificar_radicado` | Tipificar un radicado (asignar o corregir su tipo documental) | `PATCH /documents/{document_id}` | `PERM_RADI` (**3**) | **W** |
| `listar_elementos_metadato` | Listar los elementos de metadato | `GET /metadata/elements` | _no permission_ | R |
| `listar_plantillas_metadato` | Listar las plantillas de metadatos | `GET /metadata/templates` | _no permission_ | R |

**E04 · Retention schedules (TRD/TVD) and disposition** — 38 tools, 21 writes

| Tool | Operation (verbatim, as the server shows it) | Endpoint | Permission (crud) | |
| --- | --- | --- | --- | --- |
| `anular_lote_eliminacion` | Retirar un lote del procedimiento de eliminación | `POST /eliminacion/lotes/{lote_id}/anular` | `USUA_PERM_TRD` (**3**) | **W** |
| `aprobar_version_trd` | Aprobar una versión de la TRD | `POST /trd/{code}/versiones/{version}/aprobar` | `USUA_PERM_TRD` (**3**) | **W** |
| `aprobar_version_tvd` | Aprobar una versión de la TVD | `POST /tvd/{code}/versiones/{version}/aprobar` | `USUA_PERM_TRD` (**3**) | **W** |
| `avalar_lote_eliminacion` | Registrar el aval del Comité sobre un lote de eliminación | `POST /eliminacion/lotes/{lote_id}/avalar` | `USUA_PERM_TRD` (**4**) | **W** type `lote_id` |
| `convalidar_version_trd` | Convalidar una versión de la TRD | `POST /trd/{code}/versiones/{version}/convalidar` | `USUA_PERM_TRD` (**3**) | **W** type `version` |
| `convalidar_version_tvd` | Convalidar una versión de la TVD | `POST /tvd/{code}/versiones/{version}/convalidar` | `USUA_PERM_TRD` (**3**) | **W** type `version` |
| `crear_agrupacion_tvd` | Crear una agrupación de la TVD | `POST /tvd` | `USUA_PERM_TRD` (1) | **W** |
| `crear_serie_trd` | Crear una serie de la TRD | `POST /trd` | `USUA_PERM_TRD` (1) | **W** |
| `crear_tipo_documental` | Crear un tipo documental | `POST /tipos-documentales` | `USUA_PERM_TRD` (1) | **W** |
| `decidir_disposicion_vencida` | Decidir sobre una disposición vencida | `POST /trd/disposicion-vencida/{expediente_id}/decidir` | `USUA_PERM_TRD` (**3**) | **W** irreversible |
| `derogar_version_trd` | Derogar una versión de la TRD | `POST /trd/{code}/versiones/{version}/derogar` | `USUA_PERM_TRD` (**3**) | **W** type `version` |
| `derogar_version_tvd` | Derogar una versión de la TVD | `POST /tvd/{code}/versiones/{version}/derogar` | `USUA_PERM_TRD` (**3**) | **W** type `version` |
| `devolver_version_trd` | Devolver una versión de la TRD con observaciones | `POST /trd/{code}/versiones/{version}/devolver` | `USUA_PERM_TRD` (**3**) | **W** |
| `devolver_version_tvd` | Devolver una versión de la TVD con observaciones | `POST /tvd/{code}/versiones/{version}/devolver` | `USUA_PERM_TRD` (**3**) | **W** |
| `generar_acta_eliminacion` | Generar el acta de eliminación de un lote | `POST /eliminacion/lotes/{lote_id}/acta` | `USUA_PERM_TRD` (**3**) | **W** irreversible |
| `nueva_version_agrupacion_tvd` | Crear una versión nueva de una agrupación TVD | `POST /tvd/{code}/versiones` | `USUA_PERM_TRD` (**3**) | **W** |
| `nueva_version_serie_trd` | Crear una versión nueva de una serie TRD | `POST /trd/{code}/versiones` | `USUA_PERM_TRD` (**3**) | **W** |
| `proponer_lote_eliminacion` | Proponer un lote de eliminación | `POST /eliminacion/lotes` | `USUA_PERM_TRD` (**3**) | **W** |
| `publicar_inventario_lote` | Publicar el inventario de un lote de eliminación | `POST /eliminacion/lotes/{lote_id}/publicar` | `USUA_PERM_TRD` (**3**) | **W** irreversible |
| `registrar_rusd_trd` | Registrar la inscripción RUSD de una versión de la TRD | `POST /trd/{code}/versiones/{version}/registrar-rusd` | `USUA_PERM_TRD` (**3**) | **W** |
| `reintentar_firma_acta_eliminacion` | Reintentar la firma del acta de eliminación | `POST /eliminacion/lotes/{lote_id}/acta/firmar` | `USUA_PERM_TRD` (**3**) | **W** |
| `consultar_acta_eliminacion` | Consultar el acta de eliminación | `GET /eliminacion/lotes/{lote_id}/acta` | `USUA_PERM_TRD` (1) | R |
| `consultar_disposicion` | Consultar la disposición final de un radicado | `GET /documents/{document_id}/disposition` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `consultar_lote_eliminacion` | Consultar un lote de eliminación | `GET /eliminacion/lotes/{lote_id}` | `USUA_PERM_TRD` (1) | R |
| `consultar_retencion` | Calcular el calendario de retención de una serie | `GET /trd/{serie_id}/retention` | _no permission_ | R |
| `consultar_serie` | Consultar una serie de la TRD | `GET /trd/{serie_id}` | _no permission_ | R |
| `consultar_tipo_documental` | Consultar un tipo documental | `GET /tipos-documentales/{tipo_id}` | _no permission_ | R |
| `consultar_tvd` | Consultar una agrupación de la TVD | `GET /tvd/{agrupacion_id}` | _no permission_ | R |
| `disposicion_vencida` | Consultar los expedientes con disposición vencida | `GET /trd/disposicion-vencida` | `USUA_PERM_TRD` (1) | R |
| `lapidas_lote` | Consultar las lápidas de un lote eliminado | `GET /eliminacion/lotes/{lote_id}/lapidas` | `USUA_PERM_TRD` (1) | R |
| `listar_lotes_eliminacion` | Listar los lotes de eliminación | `GET /eliminacion/lotes` | `USUA_PERM_TRD` (1) | R |
| `listar_tipos_documentales` | Consultar los tipos documentales de la TRD | `GET /tipos-documentales` | _no permission_ | R |
| `listar_trd` | Consultar la Tabla de Retención Documental | `GET /trd` | _no permission_ | R |
| `listar_tvd` | Listar las agrupaciones de la TVD | `GET /tvd` | _no permission_ | R |
| `retencion_tvd` | Consultar la retención de una agrupación TVD | `GET /tvd/{agrupacion_id}/retention` | _no permission_ | R |
| `revision_trd_pendiente` | Consultar los expedientes pendientes de revisión de TRD | `GET /trd/revision-pendiente` | `USUA_PERM_TRD` (1) | R |
| `versiones_agrupacion_tvd` | Consultar el historial de versiones de una agrupación TVD | `GET /tvd/{code}/versiones` | `USUA_PERM_TRD` (1) | R |
| `versiones_serie_trd` | Consultar el historial de versiones de una serie TRD | `GET /trd/{code}/versiones` | `USUA_PERM_TRD` (1) | R |

**E05 · Workflows** — 16 tools, 9 writes

| Tool | Operation (verbatim, as the server shows it) | Endpoint | Permission (crud) | |
| --- | --- | --- | --- | --- |
| `asignar_radicado` | Asignar un radicado a una dependencia | `POST /workflows/assign` | `PERM_TRAMITAR` (1) | **W** |
| `completar_paso` | Completar un paso de trámite | `PATCH /workflows/{step_id}/complete` | `PERM_TRAMITAR` (1) | **W** |
| `crear_cadena_vobo` | Crear la cadena de vistos buenos | `POST /workflows/{radicado_id}/vistos-buenos` | `PERM_RADI` (1) | **W** |
| `decidir_visto_bueno` | Dar o negar el visto bueno | `POST /workflows/{radicado_id}/vistos-buenos/decidir` | `PERM_VOBO` (1) | **W** |
| `devolver_radicado` | Devolver un radicado con causal | `POST /workflows/{radicado_id}/devolver` | `PERM_TRAMITAR` (1) | **W** |
| `ejecutar_transaccion` | Ejecutar una transacción de trámite | `POST /workflows/{radicado_id}/transactions` | `PERM_TRAMITAR` (1) | **W** |
| `finalizar_tramite` | Finalizar el trámite de un radicado (irreversible; mandatory `motivo_cierre`: `respondido`, `nrr`, `traslado_competencia` or `otro`) | `POST /workflows/{radicado_id}/finalizar` | `PERM_TRAMITAR` (3) | **W** irreversible (confirmed by typing the radicado) |
| `revertir_asignacion` | Revertir la última asignación | `POST /workflows/{radicado_id}/rollback` | `PERM_TRAMITAR` (1) | **W** |
| `trasladar_radicado` | Trasladar un radicado a otra dependencia | `POST /workflows/transfer` | `PERM_TRAMITAR` (1) | **W** |
| `bandeja_tramite` | Consultar la bandeja de trámite | `GET /workflows/inbox` | _no permission_ | R |
| `consultar_cadena_vobo` | Consultar la cadena de vistos buenos | `GET /workflows/{radicado_id}/vistos-buenos` | _no permission_ | R |
| `eventos_radicado` | Consultar los eventos de flujo de un radicado | `GET /workflows/{radicado_id}/events` | _no permission_ | R |
| `hoja_ruta_radicado` | Consultar la hoja de ruta de un radicado | `GET /workflows/{radicado_id}/history` | _no permission_ | R |
| `paso_actual` | Consultar el paso de trámite activo de un radicado | `GET /workflows/{radicado_id}/current` | _no permission_ | R |
| `pendientes_equipo` | Consultar los pendientes de trámite del equipo | `GET /workflows/pending` | _no permission_ | R |
| `tipos_transaccion` | Consultar los tipos de transacción de trámite | `GET /workflows/transaction-types` | _no permission_ | R |

**E06 · Electronic signature** — 10 tools, 2 writes

| Tool | Operation (verbatim, as the server shows it) | Endpoint | Permission (crud) | |
| --- | --- | --- | --- | --- |
| `abrir_cadena_firma` | Abrir un circuito de firmas | `POST /signature/requests` | `PERM_FIRMA` (1) | **W** |
| `firmar_indice` | Firmar el índice electrónico | `POST /expedientes/{expediente_id}/indice/firmar` | `USUA_PERM_EXPEDIENTE` (1) | **W** |
| `cadena_firma_de_un_objeto` | Ver si un documento ya está en un circuito de firma | `GET /signature/requests` | `PERM_FIRMA` (1) | R |
| `consultar_cadena_firma` | Consultar una cadena de firmas | `GET /signature/requests/{solicitud_id}` | `PERM_FIRMA` (1) | R |
| `consultar_comprobante_firma` | Ver el comprobante de una firma | `GET /signature/firmas/{firma_id}` | _no permission_ | R |
| `estado_mi_credencial_de_firma` | Consultar el estado de mi credencial de firma | `GET /auth/me/signing-key` | _no permission_ | R |
| `estado_mi_segundo_factor` | Consultar el estado de mi segundo factor | `GET /auth/me/totp` | _no permission_ | R |
| `firmas_de_un_radicado` | Ver las firmas de un radicado | `GET /documents/{document_id}/signatures` | `PERM_FIRMA` (1) | R |
| `firmas_pendientes` | Ver qué tengo pendiente de firmar | `GET /signature/pending` | `PERM_FIRMA` (1) | R |
| `indices_pendientes_firma` | Consultar los índices pendientes de firma | `GET /expedientes/indices/pendientes-firma` | `USUA_PERM_EXPEDIENTE` (1) | R |

**E07 · Attachments** — 1 tools

| Tool | Operation (verbatim, as the server shows it) | Endpoint | Permission (crud) | |
| --- | --- | --- | --- | --- |
| `listar_anexos` | Consultar los anexos de un radicado | `GET /documents/{document_id}/anexos` | _no permission_ | R |

**E08 · Security, RBAC and audit** — 15 tools, 1 writes

| Tool | Operation (verbatim, as the server shows it) | Endpoint | Permission (crud) | |
| --- | --- | --- | --- | --- |
| `reclasificar_radicado` | Reclasificar el nivel de seguridad de un radicado | `PATCH /documents/{document_id}/security-level` | `PERM_RECLASIFICAR` (**3**) | **W** irreversible |
| `clearance_de_grupo` | Consultar el nivel de acceso de un grupo | `GET /auth/groups/{group_id}/clearance` | `USUA_PERM_ADMIN` (1) | R |
| `consultar_auditoria` | Consultar la traza de auditoría | `GET /audit` | `USUA_PERM_ADMIN` (1) | R |
| `listar_grupos` | Listar los grupos de la entidad | `GET /auth/groups` | `USUA_PERM_ADMIN` (1) | R |
| `listar_niveles_seguridad` | Consultar los niveles de clasificación | `GET /auth/security-levels` | _no permission_ | R |
| `listar_permisos_del_sistema` | Listar los permisos del sistema | `GET /auth/permissions` | `USUA_PERM_ADMIN` (1) | R |
| `listar_urd_usuario` | Consultar las unidades de reparto de una persona | `GET /auth/users/{user_id}/urd` | `USUA_PERM_ADMIN` (1) | R |
| `listar_usuarios` | Listar los usuarios de la entidad | `GET /auth/users` | `USUA_PERM_ADMIN` (1) | R |
| `listar_usuarios_asignables` | Listar usuarios a los que asignar | `GET /auth/users/pickable` | `PERM_RADI` (1) | R |
| `mi_clearance` | Consultar mi nivel de acceso | `GET /auth/clearance` | _no permission_ | R |
| `mi_contexto` | Consultar mi contexto de trabajo | `GET /auth/context` | _no permission_ | R |
| `miembros_de_grupo` | Consultar los miembros de un grupo | `GET /auth/groups/{group_id}/members` | `USUA_PERM_ADMIN` (1) | R |
| `permisos_de_grupo` | Consultar los permisos de un grupo | `GET /auth/groups/{group_id}/permissions` | `USUA_PERM_ADMIN` (1) | R |
| `permisos_de_usuario` | Consultar los permisos efectivos de una persona | `GET /auth/users/{user_id}/permissions` | `USUA_PERM_ADMIN` (1) | R |
| `verificar_cadena_auditoria` | Verificar la integridad de la auditoría | `GET /audit/verify` | `USUA_PERM_ADMIN` (1) | R |

**E09 · Search and reports** — 3 tools

| Tool | Operation (verbatim, as the server shows it) | Endpoint | Permission (crud) | |
| --- | --- | --- | --- | --- |
| `buscar_expedientes` | Buscar expedientes | `GET /expedientes/search` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `buscar_radicados` | Buscar radicados | `GET /search` | _no permission_ | R |
| `reporte_radicacion` | Ver el resumen estadístico de radicación | `GET /reports/radicados` | `SGD_PERM_ESTADISTICA` (1) | R |

**E10 · Preservation (WORM/PREMIS)** — 9 tools, 3 writes

| Tool | Operation (verbatim, as the server shows it) | Endpoint | Permission (crud) | |
| --- | --- | --- | --- | --- |
| `declarar_plan_preservacion` | Declarar una versión del plan de preservación | `PUT /preservacion/plan` | `USUA_PERM_EXPEDIENTE` (**3**) | **W** |
| `migrar_formato` | Migrar un archivo a PDF/A | `POST /preservacion/migrar-formato` | `USUA_PERM_EXPEDIENTE` (**3**) | **W** |
| `registrar_evento_preservacion` | Registrar un evento de preservación | `POST /preservacion/eventos` | `USUA_PERM_EXPEDIENTE` (**3**) | **W** irreversible |
| `artefactos_pendientes_renovacion` | Consultar los artefactos pendientes de renovación WORM | `GET /expedientes/indices/pendientes-renovacion` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `cadena_migraciones` | Consultar la cadena de migraciones de un archivo | `GET /preservacion/migraciones/{file_id}` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `consultar_aip` | Consultar el AIP de preservación de un expediente | `GET /expedientes/{expediente_id}/aip` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `consultar_plan_preservacion` | Consultar el plan de preservación digital | `GET /preservacion/plan` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `eventos_preservacion` | Consultar los eventos de preservación de un expediente | `GET /expedientes/{expediente_id}/preservacion/eventos` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `versiones_plan_preservacion` | Consultar las versiones del plan de preservación | `GET /preservacion/plan/versions` | `USUA_PERM_EXPEDIENTE` (1) | R |

**E11 · Interoperability** — 3 tools

| Tool | Operation (verbatim, as the server shows it) | Endpoint | Permission (crud) | |
| --- | --- | --- | --- | --- |
| `consultar_envio` | Consultar un envío | `GET /envios/{shipment_id}` | `PERM_RADI_SALIDA` (1) | R |
| `listar_webhooks` | Listar los canales de salida configurados | `GET /webhooks` | `USUA_PERM_ADMIN` (1) | R |
| `rastrear_envio` | Rastrear un envío con el operador | `GET /envios/{shipment_id}/tracking` | `PERM_RADI_SALIDA` (1) | R |

**E12 · Transfers** — 9 tools, 4 writes

| Tool | Operation (verbatim, as the server shows it) | Endpoint | Permission (crud) | |
| --- | --- | --- | --- | --- |
| `crear_transferencia` | Crear una transferencia | `POST /transferencias` | `USUA_PERM_EXPEDIENTE` (**3**) | **W** |
| `enviar_transferencia` | Enviar una transferencia al receptor | `POST /transferencias/{transferencia_id}/enviar` | `USUA_PERM_EXPEDIENTE` (**3**) | **W** irreversible |
| `rechazar_transferencia` | Rechazar una transferencia | `POST /transferencias/{transferencia_id}/rechazar` | `USUA_PERM_EXPEDIENTE` (**3**) | **W** irreversible |
| `recibir_transferencia` | Recibir una transferencia | `POST /transferencias/{transferencia_id}/recibir` | `USUA_PERM_EXPEDIENTE` (**3**) | **W** irreversible |
| `consultar_acta_transferencia` | Consultar el acta de una transferencia | `GET /transferencias/{transferencia_id}/acta` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `consultar_fuid` | Consultar el inventario documental (FUID) de una transferencia | `GET /transferencias/{transferencia_id}/fuid` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `consultar_transferencia` | Consultar una transferencia | `GET /transferencias/{transferencia_id}` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `listar_transferencias` | Listar las transferencias | `GET /transferencias` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `verificar_acta_transferencia` | Verificar las firmas del acta | `GET /transferencias/{transferencia_id}/acta/verificacion` | `USUA_PERM_EXPEDIENTE` (1) | R |

**E13 · Public counter** — 2 tools, 2 writes

| Tool | Operation (verbatim, as the server shows it) | Endpoint | Permission (crud) | |
| --- | --- | --- | --- | --- |
| `publicar_de_oficio` | Publicar de oficio un radicado | `POST /documents/{document_id}/publicacion` | `PERM_RECLASIFICAR` (**3**) | **W** irreversible |
| `publicar_expediente` | Publicar de oficio un expediente | `POST /expedientes/{expediente_id}/publicacion` | `PERM_RECLASIFICAR` (**3**) | **W** irreversible |

**E14 · Administration and PINAR** — 19 tools, 1 writes

| Tool | Operation (verbatim, as the server shows it) | Endpoint | Permission (crud) | |
| --- | --- | --- | --- | --- |
| `registrar_avance_pinar` | Registrar el avance de un proyecto PINAR | `POST /pinar/proyectos/{proyecto_id}/seguimiento` | `USUA_PERM_ADMIN` (3) | **W** |
| `arbol_dependencias` | Consultar el árbol de dependencias | `GET /dependencias/tree` | _no permission_ | R |
| `calcular_dias_habiles` | Calcular el vencimiento en días hábiles | `GET /business-days/calculate` | _no permission_ | R |
| `consultar_dependencia` | Consultar una dependencia | `GET /dependencias/{dependencia_id}` | _no permission_ | R |
| `consultar_dependencia_por_codigo` | Consultar una dependencia por su código | `GET /dependencias/by-codigo/{codigo}` | _no permission_ | R |
| `consultar_parametro` | Consultar un parámetro de la entidad | `GET /config/params/{key}` | _no permission_ | R |
| `consultar_plan_pinar` | Consultar un plan PINAR | `GET /pinar/planes/{plan_id}` | `USUA_PERM_ADMIN` (1) | R |
| `listar_catalogos` | Listar los catálogos de vocabulario | `GET /catalogos` | _no permission_ | R |
| `listar_dependencias` | Listar las dependencias de la entidad | `GET /dependencias` | _no permission_ | R |
| `listar_festivos` | Consultar los festivos de la entidad | `GET /config/holidays` | _no permission_ | R |
| `listar_items_catalogo` | Consultar los valores de un catálogo | `GET /catalogos/{catalogo}` | _no permission_ | R |
| `listar_parametros` | Consultar los parámetros de la entidad | `GET /config/params` | _no permission_ | R |
| `listar_planes_pinar` | Listar los planes PINAR | `GET /pinar/planes` | `USUA_PERM_ADMIN` (1) | R |
| `mapa_ruta_pinar` | Consultar el mapa de ruta de un plan PINAR | `GET /pinar/planes/{plan_id}/mapa-ruta` | `USUA_PERM_ADMIN` (1) | R |
| `objetivos_pinar` | Consultar los objetivos de un plan PINAR | `GET /pinar/planes/{plan_id}/objetivos` | `USUA_PERM_ADMIN` (1) | R |
| `priorizacion_pinar` | Consultar la priorización de un plan PINAR | `GET /pinar/planes/{plan_id}/priorizacion` | `USUA_PERM_ADMIN` (1) | R |
| `proyectos_pinar` | Consultar los proyectos de un plan PINAR | `GET /pinar/planes/{plan_id}/proyectos` | `USUA_PERM_ADMIN` (1) | R |
| `seguimiento_proyecto_pinar` | Consultar el seguimiento de un proyecto PINAR | `GET /pinar/proyectos/{proyecto_id}/seguimiento` | `USUA_PERM_ADMIN` (1) | R |
| `tablero_pinar` | Consultar el tablero de un plan PINAR | `GET /pinar/planes/{plan_id}/tablero` | `USUA_PERM_ADMIN` (1) | R |

**E15 · Electronic index** — 6 tools

| Tool | Operation (verbatim, as the server shows it) | Endpoint | Permission (crud) | |
| --- | --- | --- | --- | --- |
| `historial_acceso_expediente` | Consultar el historial de acceso al expediente | `GET /expedientes/{expediente_id}/historial-acceso` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `indice_expediente` | Consultar el índice electrónico de un expediente | `GET /expedientes/{expediente_id}/indice` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `indice_xml` | Obtener el índice electrónico en XML | `GET /expedientes/{expediente_id}/indice.xml` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `roles_autorizados_expediente` | Consultar los grupos autorizados a leer el expediente | `GET /expedientes/{expediente_id}/roles-autorizados` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `verificar_indice` | Verificar la integridad del índice electrónico | `GET /expedientes/{expediente_id}/indice/verify` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `versiones_indice` | Listar las versiones del índice electrónico | `GET /expedientes/{expediente_id}/indice/versions` | `USUA_PERM_EXPEDIENTE` (1) | R |

**E16 · Notifications** — 6 tools, 2 writes

| Tool | Operation (verbatim, as the server shows it) | Endpoint | Permission (crud) | |
| --- | --- | --- | --- | --- |
| `descartar_evento_atascado` | Descartar un evento fallido | `POST /notification/admin/deadletter/{event_id}/discard` | `PERM_DLQ_ADMIN` (1) | **W** irreversible |
| `reintentar_evento_atascado` | Reintentar un evento fallido | `POST /notification/admin/deadletter/{event_id}/replay` | `PERM_DLQ_ADMIN` (1) | **W** irreversible |
| `consultar_evento_atascado` | Consultar un evento fallido | `GET /notification/admin/deadletter/{event_id}` | `PERM_DLQ_ADMIN` (1) | R |
| `consultar_notificacion` | Consultar una notificación | `GET /notifications/{notification_id}` | `USUA_PERM_ADMIN` (1) | R |
| `historial_notificaciones` | Ver el historial de notificaciones | `GET /notifications/history` | `USUA_PERM_ADMIN` (1) | R |
| `listar_eventos_atascados` | Ver la cola de eventos fallidos | `GET /notification/admin/deadletter/` | `PERM_DLQ_ADMIN` (1) | R |

**E17 · Physical archive** — 13 tools, 5 writes

| Tool | Operation (verbatim, as the server shows it) | Endpoint | Permission (crud) | |
| --- | --- | --- | --- | --- |
| `crear_ubicacion` | Crear una ubicación en el archivo físico | `POST /ubicaciones` | `USUA_PERM_EXPEDIENTE` (1) | **W** |
| `crear_unidad` | Crear una unidad de conservación | `POST /unidades` | `USUA_PERM_EXPEDIENTE` (1) | **W** |
| `devolver_prestamo` | Registrar la devolución de un préstamo | `POST /prestamos/{prestamo_id}/devolver` | `USUA_PERM_EXPEDIENTE` (1) | **W** |
| `registrar_prestamo` | Registrar el préstamo de una unidad | `POST /unidades/{unidad_id}/prestamos` | `USUA_PERM_EXPEDIENTE` (1) | **W** |
| `ubicar_expediente_en_unidad` | Ubicar un expediente en una unidad | `POST /unidades/{unidad_id}/expedientes` | `USUA_PERM_EXPEDIENTE` (1) | **W** |
| `consultar_ubicacion` | Consultar una ubicación | `GET /ubicaciones/{ubicacion_id}` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `contenido_unidad` | Consultar el contenido de una unidad | `GET /unidades/{unidad_id}/expedientes` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `inventario_documental` | Consultar el inventario documental | `GET /fuid` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `listar_prestamos` | Consultar los préstamos del archivo físico | `GET /prestamos` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `listar_ubicaciones` | Listar las ubicaciones del archivo físico | `GET /ubicaciones` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `listar_unidades` | Listar las unidades de conservación | `GET /unidades` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `movimientos_unidad` | Consultar los movimientos de una unidad | `GET /unidades/{unidad_id}/movimientos` | `USUA_PERM_EXPEDIENTE` (1) | R |
| `ubicacion_expediente` | Localizar físicamente un expediente | `GET /expedientes/{expediente_id}/unidades` | `USUA_PERM_EXPEDIENTE` (1) | R |

**E19 · Email ingestion** — 8 tools, 5 writes

| Tool | Operation (verbatim, as the server shows it) | Endpoint | Permission (crud) | |
| --- | --- | --- | --- | --- |
| `descartar_correo` | Descartar un correo sin radicarlo | `POST /email/cuentas/{cuenta_id}/bandeja/{mensaje_id}/descartar` | `PERM_RADI` (1) | **W** irreversible |
| `liberar_correo` | Liberar la reserva de un correo | `POST /email/cuentas/{cuenta_id}/bandeja/{mensaje_id}/liberar` | `PERM_RADI` (1) | **W** |
| `radicar_correo` | Radicar un correo como documento de entrada | `POST /email/cuentas/{cuenta_id}/bandeja/{mensaje_id}/radicar` | `PERM_RADI` (1) | **W** irreversible |
| `reservar_correo` | Reservar un correo para trabajarlo | `POST /email/cuentas/{cuenta_id}/bandeja/{mensaje_id}/reservar` | `PERM_RADI` (1) | **W** |
| `sincronizar_buzon` | Traer los correos nuevos de un buzón | `POST /email/cuentas/{cuenta_id}/bandeja/sincronizar` | `PERM_RADI` (1) | **W** |
| `estado_bandeja_correo` | Consultar el resumen de un buzón | `GET /email/cuentas/{cuenta_id}/bandeja/estado` | `PERM_RADI` (1) | R |
| `listar_buzones_correo` | Listar los buzones de correo que puedo trabajar | `GET /email/cuentas` | `PERM_RADI` (1) | R |
| `listar_correos_pendientes` | Ver los correos de un buzón | `GET /email/cuentas/{cuenta_id}/bandeja` | `PERM_RADI` (1) | R |

**E20 · Postal operator** — 4 tools, 2 writes

| Tool | Operation (verbatim, as the server shows it) | Endpoint | Permission (crud) | |
| --- | --- | --- | --- | --- |
| `actualizar_estado_envio` | Actualizar el estado de un envío | `POST /envios/{shipment_id}/estado` | `PERM_RADI_SALIDA` (3) | **W** |
| `despachar_envio` | Despachar un radicado por correo postal | `POST /documents/{document_id}/envios` | `PERM_RADI_SALIDA` (3) | **W** |
| `bandeja_envios` | Consultar la bandeja de envíos | `GET /envios` | `PERM_RADI_SALIDA` (1) | R |
| `consultar_envios_radicado` | Consultar los envíos de un radicado | `GET /documents/{document_id}/envios` | `PERM_RADI_SALIDA` (1) | R |

**E21 · Knowledge and RAG** — 3 tools

| Tool | Operation (verbatim, as the server shows it) | Endpoint | Permission (crud) | |
| --- | --- | --- | --- | --- |
| `buscar_antecedentes` | Buscar antecedentes de un asunto o de un radicado | `POST /knowledge/antecedentes` | _no permission_ | R |
| `buscar_conocimiento` | Buscar en la base de conocimiento documental | `POST /knowledge/search` | _no permission_ | R |
| `redactar_con_antecedentes` | Redactar un texto apoyado en antecedentes, con citas | `POST /knowledge/rag` | _no permission_ | R |

**E23 · Document production** — 3 tools, 1 writes

| Tool | Operation (verbatim, as the server shows it) | Endpoint | Permission (crud) | |
| --- | --- | --- | --- | --- |
| `devolver_borrador_a_edicion` | Devolver un borrador aprobado a edición | `POST /borradores/{borrador_id}/devolver` | `PERM_RADI` (1) | **W** irreversible |
| `campos_de_plantilla` | Ver los campos disponibles de una plantilla | `GET /plantillas/campos` | `PERM_RADI` (1) | R |
| `combinar_plantilla` | Previsualizar una plantilla combinada | `POST /plantillas/{plantilla_id}/combinar` | `PERM_RADI` (1) | R |

**Mutual exclusion declared, not discovered through a 422.** `buscar_antecedentes` takes either the subject (`query`) or the reference tracking number (`radicado_ref`), never both and never neither — enforced by `AntecedentesRequest` with a `model_validator`, and JSON Schema cannot express it with the catalogue's building blocks. The catalogue declares it via `exactly_one_of` and `coerce_args` answers `parametros_excluyentes` **before** the request leaves: the backend's 422 says the same thing, only after making the person wait.

**`aprobar_borrador` IS required** (fixed 2026-08-17). `POST /borradores/{id}/radicar` now answers `409 borrador_sin_aprobar` for an unapproved draft. Previously only the screen enforced it (`canRadicar = estado === "aprobado"`), so a direct `POST` skipped approval and consumed the number anyway — a control living only in the browser is not a control.

**Irreversible acts: a typed phrase, enforced by the backend.** A proposal whose tool declares `destructive` carries `irreversible: true` and `frase_requerida: "CONFIRMO"`, and executing it requires sending that phrase (`{token, frase}`). Who says the act is irreversible is **not the client**: it is the signed token, which the tool is read from. The UI disables the button as a courtesy; the barrier lives in `write_gate`, because a phrase validated only in the browser is theatre.

**The conversational assistant proposes too.** `POST /api/v1/assistant/message` returns `propuestas: []` (additive field) with the acts the model prepared. The `tool_result` fed back to the model explicitly says `ejecutado: false` so it cannot claim to have done something it did not, and the token never enters its context. Confirmation is `POST /api/v1/assistant/confirmar` with `{token, frase?}`.

**Channel traceability (AREA-11-04).** Everything the mcp-server does on behalf of a person —the assistant, MCP, confirming a proposal— reaches the gateway through `/api/v1/<domain>` and used to be recorded as channel `api`, like a direct call. Every outgoing call now carries `X-Origen-Canal: mcp.<ts>.<hmac>`: a mark signed with `MCP_ORIGIN_SECRET` (dedicated secret, held only by the gateway and the mcp-server, distinct from the master `IDENTITY_SIGNING_SECRET`), **bound to the tenant and the token** (sha256 of `Authorization`) and valid for 120 s. The gateway verifies it and only then derives `X-Canal: mcp` (`derive_canal`); with no mark, a bad or expired signature, or another token/entity, the channel is `api`, and with no secret configured `mcp` is never attributed. The header is not forwarded downstream. Entries with channel `mcp` also carry `payload.correlacion = {request_id, idempotency_key}` (the turn's `X-Request-Id` and the proposal `jti` issued as `Idempotency-Key`), tying the entry to the turn and the proposal that authorised it; other channels are unchanged. **What the model cannot invent:** `POST /assistant/message` also returns `no_verificados: string[]` (additive, almost always empty): any number shaped `{YYYY}-{TENANT}-{E|S|I}-{NNNNNN}` in the final text that does **not** appear in some `tool_result` of the conversation —what the system returned; what the person typed does not count— is listed there and the text gets a «No verificado» notice. It is not removed from the text: the person must see what they were told.

**Client IP (AREA-11-08).** The mcp-server forwards the real client IP to the gateway in `X-Forwarded-For` —only the one the gateway signed with the origin secret `MCP_ORIGIN_SECRET` (`X-Real-Ip` + `X-Real-Ip-Signature`, bound to IP, person and entity; the mcp-server does NOT hold the master identity secret); with no signature or no secret it forwards nothing—, so the assistant calls of ALL entities no longer fall into the mcp-server container's per-IP bucket. Each turn logs `iteraciones`, `llamadas_gateway` (what it spends from the person's bucket), `propuestas` and `no_verificados`.

**Left out by decision, not by oversight — 15 declared acts.** They are not merely absent: the server publishes each one with its reason and its alternative route in `mi_alcance` and the `orfeo://alcance` resource, because a silently trimmed catalogue makes the limit discoverable only by asking for something that is never going to happen. There are four criteria, and they protect different things:

1. **Acts whose guarantee is a person being present.** A confirmation card raises the cost of carelessness; it does not turn a model into a person. Here belong **executing the deletion of a disposal batch** (the four prior phases — propose, publish, endorse and the minutes — are offered; the execution never is), **signing, approving or rejecting a turn in a signature chain**, **personally signing a transfer's minutes** and **verifying a signature's integrity**.
2. **Acts that move the authorisation boundary itself.** The whole framework is about working *within* the asker's limits, and an act that widens them stops being a limit: **granting or revoking permissions, changing a group's clearance, creating people and groups**.
3. **Acts that take a one-time secret into a channel that does not hold it**: **issuing or revoking personal credentials** — TOTP, recovery codes, signing key. They are revealed exactly once by design; handing them over here would leave them written in a conversation's history.
4. **The rule everything else is measured against**, not a dangerous act but the pattern itself: **changing the holiday calendar or the tenant's parameters** (a holiday added today moves deadlines already running) and **adding catalogue values or changing the dependency structure** (a catalogue that gains entries from a conversation stops being closed, which was the only thing that made it useful).

The list is completed by the acts **this channel cannot present honestly**: **sealing an artefact under WORM** and **packaging the AIP** (both need an internal identifier the asker would have to invent, and a ten-year lock cannot be shortened), **assembling a PINAR plan's structure** (planning with the Committee present, not a sequence of inserts), **previewing a draft's PDF and creating or deleting templates** (a PDF does not fit in a conversation), **configuring mail accounts**, **working a personal mailbox** and **registering or removing a webhook**. And **bulk linking** (`/radicados/batch`, up to 500) stays out for as long as its confirmation card cannot be read: a card that says "500 radicados" does not let anyone confirm knowingly, which is the only reason the card exists.

**Two cross-service invariants** live in `shared/tests/`, the only CI lane that sees the whole repository: `test_rutas_mcp_alcanzables.py` (every catalogue path is in the gateway's routing table — the gap that already slipped through with bulk upload) and `test_permisos_declarados.py` (what the catalogue declares is what the endpoint requires, in both directions: under-declaring offers the tool to someone who will get a 403; over-declaring hides it from someone who can actually use it).

### Conversational assistant (E18, ADR-019/ADR-020) — via the gateway

Unlike the MCP catalog/protocol (which is **not** exposed through the gateway), the conversational assistant **is** routed by the `api-gateway` (`/api/v1/assistant/` → mcp-server), so it inherits its JWT validation and `X-Tenant-Slug` injection. The mcp-server orchestrates an **LLM loop (Claude)** that executes the catalog's **read-only** tools as calls to the gateway, **re-propagating the user's token** — the assistant runs _as-the-user_, never with credentials of its own, and every tool revalidates its RBAC/clearance in the backend.

- `POST /api/v1/assistant/message` `{mensaje: string(1..4000), conversation_id?: string}` — sends one user turn. Returns `{conversation_id, message: {id, role:"assistant", texto, formato:"markdown", ts}}`. The `conversation_id` (generated if omitted) keeps the thread; the history is **ephemeral per process** and **isolated per tenant** (a `conversation_id` never crosses tenants). **The assistant offers all 207 tools, and the 75 writes are NOT dispatched**: on a write `tool_use` the server signs a proposal and returns a `tool_result` stating `ejecutado: false` (`assistant.py`). That anchor matters: without it the model reads its own `tool_use` as having taken effect and writes "I have registered the document", asserting a false fact about an official record — the no-invention rule applied to its own acts. The signed token **never enters its context**; the person confirms it in `ProposalCard`. Arguments are also trimmed to the declared schema before dispatch (an undeclared body key does not travel, even though the server-side clearance ceiling would already stop it). Content returned by the tools (e.g. inbound radicados drafted by third parties) is **data, never instruction** (an anti-injection boundary in the system prompt). `503 assistant_unavailable` if the assistant is not configured (no `ANTHROPIC_API_KEY`) or the SDK fails — **honest degradation**, never a simulated answer (RF-FIR-15); `401` without a token; `409 conversation_limit` when the thread's turn cap is exhausted. Tool `403`/`404` responses are communicated **generically** ("I could not find that record"), without revealing the existence of classified records (non-oracle, RF-SEG-08 / Law 1712/2014).

### Knowledge layer (E21, ADR-006) — knowledge-service, port 8011

An **advisory/derived and opt-in** layer (not a source of truth). Embeddings with a pluggable provider (local stub by default).

- `POST /api/v1/knowledge/ingest` `{source_type, source_ref, texto, metadata?, acl?}` — computes the embedding and persists the fragment (pgvector) with its ACL. To protect classified content, the ingester must include the level in `acl.nivel_seguridad` (1–3); absent = PUBLIC (1).
- `POST /api/v1/knowledge/search` `{query, top_k?, acl?}` — semantic retrieval (cosine kNN). **Access control resolved on the server (RF-SEG-08 / RF-BUS-04):** only fragments with `nivel_seguridad ≤` the caller's clearance are **ever** returned (derived from the JWT/DB, not from the request body); the client's optional `acl` only narrows further (e.g. by dependencia), it never widens access. Returns `[{id, source_type, source_ref, texto, metadata, score}]`. Tombstones (fragments purged by reclassification) are **excluded** from search.
- `POST /api/v1/knowledge/antecedentes` `{query|radicado_ref, source_type?, top_k?}` (XOR `query`/`radicado_ref`, E21 Inc.2, Pattern A) — pure ANCHORED and CITED retrieval, without an LLM: it reuses `search` (same ACL pre-filter) and returns `{antecedentes:[{source_type, source_ref, snippet, score}], advisory}` with a **fixed** notice (CC-01/CC-02). A non-existent `radicado_ref` **or** one exceeding the requester's clearance collapse to the same `404 antecedente_source_not_found` (anti-oracle, with no differentiated 403).
- `POST /api/v1/knowledge/rag` `{query, top_k?}` (E21 Inc.3, Pattern B) — generative RAG with citations. It retrieves with the same ACL pre-filter as `search` and, if `AI_PROVIDER` is a real generation provider (`ollama_local`, the sovereign default; `ollama_cloud`/`openai_compatible` external; `anthropic` with the native Citations API), it synthesises an ANCHORED answer. **Hard sovereignty barrier (no override)**: with an external provider it excludes from the context sent to the LLM any fragment at `nivel_seguridad ≥ RESERVED` — visible under ACL, but never egressed to a third party; if no generatable context remains, it falls back to pure retrieval (Pattern A) over everything retrieved. `AI_PROVIDER=disabled` (or unconfigured) also always falls back to Pattern A. Returns `{answer, citations:[{cited_text, source_type, source_ref, score}], proveedor, modelo, confianza, suggestion_id, advisory}` — `answer`/`modelo`/`suggestion_id` are `null` when there was no real generation. Every real generation is recorded in `kb_suggestions` (CC-03: `proveedor`/`modelo` in separate columns) and in `audit_log`. `503 rag_unavailable` on any provider failure (missing SDK, missing credential, network/API error) — honest degradation (RF-FIR-15), never a simulated answer. **`ollama_local` is sovereign by its destination, not its name (AREA-11-12)**: it is treated as local only if the `OLLAMA_URL` host is loopback, private-network, a container hostname or `*.local`; with a public host (`https://ollama.example.com`) RAG excludes every chunk at `nivel_seguridad ≥ RESERVED` from the context, as with an external provider.
- `POST /api/v1/knowledge/suggestions/{id}/aceptar` (AREA-11-11, CC-02) — **human acceptance** of a RAG suggestion: the layer suggests, the person decides. Only **whoever generated it** can accept it (`kb_suggestions.creada_por`, migration `012`; the condition is in the `WHERE`): neutral `404 suggestion_not_found` if it does not exist, belongs to someone else or predates the migration (no creator, not acceptable), `409 suggestion_already_accepted` if already accepted, `401` without identity. Returns `{id, aceptada_por, aceptada_en}` —never the content— and writes the `rag.aceptada` entry to `audit_log` in the **same transaction** as the acceptance, with `payload = {aceptada_en, sha256_texto, proveedor, modelo, fuentes}`: the hash of the accepted text, provider/model and the cited `source_ref`s (without their content), so the immutable record proves what was accepted.

> **Event-driven ingestion (E21, MVP):** besides the manual `POST /ingest`, the knowledge-service **consumes `orpycamcp.document.events`** (consumer group `knowledge-ingest` + DLQ, ADR-021) and auto-indexes every radicado on creation (`document.radicado.created` → `subject`/`sender`, `source_type="radicado"`, `source_ref=tracking_number`). The `nivel_seguridad` is **replicated from the event** (fail-closed to CLASSIFIED if absent) and **kept consistent under reclassification** (`document.radicado.reclassified`): an upgrade to RESERVED+ with an external embedding provider **purges the content** (tombstone), and a fragment's level **never goes down** except by an explicit reclassification, whatever order the events arrive in. RESERVED+ material is **not embedded** with an external provider (it is recorded in `audit_log`). An **annulled** radicado (`document.radicado.annulled`) is marked _voided_ **permanently** and stays excluded from search at any clearance (it is not re-indexed even if a creation event is reprocessed later). This is a **derived/advisory** layer (ADR-006): it is not a source of truth; the legal record lives in document/archive.

### Outbound webhooks (E11, ADR-018)

- `POST /api/v1/webhooks` `{url, event_types: [...], secret?}` — subscribes an external endpoint (empty `event_types` = all events). The `secret` is **stored encrypted at rest** (AES-256-GCM, notification-service's own KEK, tenant+webhook AAD) and is **never returned** (neither on creation nor in `GET /webhooks`); the `X-OrpycaMCP-Signature` of each delivery is computed by decrypting it in memory only. `503 webhook_secret_kek_unavailable` when the service has no usable KEK at creation time. A subscription whose secret cannot be decrypted (lost KEK, legacy row not yet encrypted) is **not delivered** until repaired. **Requires `USUA_PERM_ADMIN` at level 3** (create/delete; list needs level 1) and writes the `notification.webhook.created` audit entry with the actor (`X-User-Id`, `401` if missing) in the same transaction — the entry keeps only `scheme://host:port` plus `url_sha256` (never credentials, query or **path**, where Slack/Teams/Discord carry the token) and never the `secret`; without the audit module it answers `503 audit_unavailable` and creates nothing: registering a subscription delivers every tenant event to the given URL, so it is administrative configuration — without the gate it would be an exfiltration channel (and SSRF). **New `422`** if `url` points at an internal/non-routable destination (loopback `127.0.0.0/8`/`::1`/`localhost`, link-local `169.254.0.0/16`/`fe80::/10` — which covers the cloud metadata endpoint, private `10.0.0.0/8`/`172.16.0.0/12`/`192.168.0.0/16`/`fc00::/7`, an OrpycaMCP internal Docker network service name, or a scheme other than `http`/`https`): the `detail` explains the concrete reason for the rejection, not a generic "invalid URL". Strict by default; relaxable only in development with `WEBHOOK_ALLOW_PRIVATE_TARGETS=true` in notification-service (a kill switch, never in production).
- `GET /api/v1/webhooks` — lists the subscriptions (`X-Total-Count`). Requires `USUA_PERM_ADMIN`.
- `GET /api/v1/webhooks/{id}/entregas?page=1&size=20` — delivery record of a subscription (newest first; `X-Total-Count`; `size` ≤ 200). Requires `USUA_PERM_ADMIN`. Each attempt leaves **two** immutable rows (`webhook_entregas`, append-only): `fase=envio` — written BEFORE the POST; if it cannot be written nothing is sent — and `fase=resultado` with `entregado` / `http_error` / `error_red` / `destino_rechazado` / `secreto_inutilizable` (the last two never send and leave only `resultado`). Fields: `entrega_id`, `webhook_id`, `url_destino` (only `scheme://host:port`) + `url_sha256`, `event_id`, `event_type`, `intento`, `body_sha256` (never the body), `recortado`, `firmado`, `http_status`, `error` (≤ 500, URL-free), `at`. No `404` if the subscription no longer exists: the record outlives the deletion. Retention/purge: pending (ADR-018).
- `DELETE /api/v1/webhooks/{id}` — deletes a subscription. Requires `USUA_PERM_ADMIN` level 3 and writes `notification.webhook.deleted` with the actor (same reduced URL + `url_sha256`). `404 webhook_not_found` if it does not exist.
- **Webhook content**: the envelope is trimmed by the effective level (max of `nivel_seguridad`, `nivel_nuevo`, `nivel_anterior`): from RESERVADA (2) subject, sender, observations and the dependency route (`from_dept`, `to_dept`, `assigned_to`, `dest_dept`, `dest_dept_code`, `origin_dept`) are removed; `causal_reserva`, `fundamento_juridico` and `plazo_reserva_meses` never leave. A `document.*`/`workflow.*` event that declares no level goes out minimal (type, id, dates). Delivery connects to the validated IP (original `Host` and SNI) and follows no redirects.
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

**Every item must carry the radicado's minimum data (T13 / AREA-09-02):** `doc_class` and, by type, `dest_dept_code` (E, I) or `origin_dept_code` (S). An item without them rejects the **whole request** with `422`; the department name is resolved against the tenant's `dependencias` table (a missing or inactive one leaves the item `failed` **before** a sequence number is spent). A batch queued before this change, without codes, ends with those items `failed`.

Processing runs in a background task **with its own pool connection** — never the request's, which goes back to the pool as soon as the response is sent (see `acquire_tenant` in `app/core/database.py`). A job that cannot be processed ends `failed` with its reason in `error_message`: `pending` means still queued, not that something went wrong. `pages` is optional in the request and is recorded as `0` when omitted, as in the synchronous path. **Since T4** each item is validated against the active metadata template of its `doc_class` (if it does not fit, the item ends `failed` with `error_tipo: MetadataValidationError`), the radicado stores `metadata_template_id`, and the number's year is the registration time zone's, not UTC's.

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

Monitors the job and exposes the **per-item error detail**. Requires `PERM_RADI`. **Scope (2026-10 audit):** only **the job's creator** or whoever holds `PERM_RADI` at CRUD level >= 3 can see it; for anyone else the job "does not exist": `404 {"error": "lote_no_encontrado", "detail": "No existe el trabajo solicitado", "status": 404}` (the same body as an invented id; **`detail` used to be a string**). It needs `X-User-Id` (`400` without it). **No read-up on `subject`:** the subject of an item whose radicado is above the caller's access level (e.g. it was reclassified to reserved afterwards) comes back `null` (as does the new per-item `nivel_seguridad` field: `null` when there is no radicado or it is out of reach); that of an item with no radicado (pending or failed) is what the caller itself sent.

```json
// Response 200
{
  "job_id": "uuid",
  "job_type": "documents",
  "status": "completado_con_errores", // pending | processing | completed | completado_con_errores | failed
  "total_items": 3,
  "processed_items": 2,   // items in 'success' (recounted from the items)
  "failed_items": 1,      // items in 'failed'
  "pending_items": 0,     // missing: not processed yet (interrupted job)
  "reprocesable": true,   // failed/missing items AND the job has finished -> offer "Process the missing ones"
  "reanudaciones": 0,     // times the sweep resumed this job after a restart
  "reprocesado_at": null,
  "items": [
    { "item_index": 0, "status": "success", "result_id": "uuid-radicado", "subject": "Request 1", "intentos": 1 },
    { "item_index": 1, "status": "failed", "subject": "Request 2", "intentos": 1,
      "error_message": "Missing required field expediente_ref", "error_tipo": "MetadataValidationError" },
    { "item_index": 2, "status": "success", "result_id": "uuid-radicado", "subject": "Request 3", "intentos": 1 }
  ],
  "errores": [ /* subset of `items` with status = 'failed', same keys */ ]
}
```

Job states: `pending` (queued or interrupted; the sweep resumes it), `processing`, `completed` (no failures), **`completado_con_errores`** (finished with `failed` items: see `errores`), `failed` (reserved for a job that blew up **outside the items**, reason in `error_message`). Item states: `pending` | `success` | `failed`. **UI contract**: show `errores[]` (subject + `error_message`), and when `reprocesable` is `true` offer "Process the missing ones" → `POST .../reprocesar`; while the job is `pending`/`processing` it is not offered (the sweep resumes it on its own).

### `POST /api/v1/batch/documents/{job_id}/reprocesar`

Reprocesses **only** the `failed` items (and any missing `pending` ones). Requires `PERM_RADI` **and** being the job's creator, or holding `PERM_RADI` at CRUD level >= 3 (2026-10 audit: any `PERM_RADI` used to be enough). For anyone else the job does not exist: `404 lote_no_encontrado`, checked **before** taking the lock, so a third party cannot tell "it exists" apart through a `409 lote_en_proceso`. Returns `202`:

```json
{ "job_id": "uuid", "status": "pending", "items_a_procesar": 1 }
```

**Idempotent, with no double numbering**: items already registered (`success`) are untouched and keep their number; each item runs in its own transaction (consecutive number, radicado, success mark and audit entry together or none), so a failing item leaves no gap and an interrupted one leaves nothing. It writes the `document.lote_reprocesado` entry (actor = `X-User-Id`; the radicados created by that reprocess are registered in their name) with, for each failed item, `item_index`, `error_tipo`, **`error_message`** (truncated to 500) and **`intentos`**: the row clears them when it goes back to `pending`, so the entry is the only thing that keeps why it failed. Errors: `404 lote_no_encontrado`, `409 lote_en_proceso` (another process is working on it right now), `409 nada_que_reprocesar` (nothing failed or missing). _(The route sits under `/documents/` so the api-gateway forwards it to document-service by prefix, like `status`.)_

**Resumption and several replicas (AREA-09-15).** The job no longer depends on the process that queued it: a **sweep** (at startup after 20 s, and every 5 min) resumes `pending`/`processing` jobs older than 120 s that nobody is processing (`document.lote_reanudado`; **the entry is made in the system's name** —`actor` = `00000000-0000-0000-0000-000000005ec1`, `canal` = `system`— with `en_nombre_de` = whoever queued the job in the payload, and the same goes for the `document.radicado_created` entries of the items that resumption registers; the radicado's `registered_by` stays the job's creator) and removes `completed` jobs without errors older than 90 days (`BATCH_RETENCION_DIAS`; `completado_con_errores` and `failed` ones are kept for review). Each job runs under a session **advisory lock** per (tenant, job): two replicas never process it at once, and if the process dies the lock dies with its connection. Settings: `BARRIDO_ENABLED`, `BARRIDO_INTERVALO_SEGUNDOS`, `BATCH_REANUDAR_DESPUES_SEGUNDOS`, `BATCH_RETENCION_DIAS`.

**Audit trail of a batch radicado.** Each radicado created by the batch writes `document.radicado_created` whose payload carries `origen: "lote"`, `job_id` and `item_index` (the job and its items are removed after 90 days, the entry is not; without this a batch radicado was indistinguishable from a hand-registered one).

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

- `GET/POST/DELETE /api/v1/plantillas` — reusable templates (`nombre`, `cuerpo`, `tipo_documental`). **`POST` validates the merge markers**: a body carrying a marker that is not in the vocabulary is rejected with `422 marcador_desconocido` plus the list of the offending ones (E23, RF-PRO-01). The rejection happens **on save**, not on use: in the legacy system a mistyped marker never failed and ended up printed in the letter the citizen received.
- `GET /api/v1/plantillas` accepts `?todas=true`, which **requires `USUA_PERM_ADMIN`** (`403` otherwise). Without it the listing is narrowed to the templates the caller reaches from their dependencias (E23, RF-PRO-06): global ones, plus those assigned to one of their active URDs. This is not a security restriction — a template holds nobody's data — but a usability one: with forty in the list, the one you need cannot be found. A user **with no active URD sees only the global ones**, not all of them: emptiness is not a shortcut to "unrestricted". A narrowing the caller could switch off with a query parameter would not be a narrowing, hence the permission.
- `PUT /api/v1/plantillas/{id}/alcance` `{es_global, dependencias[]}` — which dependencias a template reaches (parity with `sgd_plan_plantillas.depe_codi`). Requires `USUA_PERM_ADMIN`. Setting `es_global=true` **clears** the list: keeping it stored would leave an invisible assignment that would reappear the moment someone restricted the template again. Restricting it to **no** dependencia is a valid state — it withdraws the template from circulation without deleting the documents already produced with it.
- `POST /api/v1/plantillas/importar` (multipart, `file`) → `{cuerpo, perdido[], marcadores_desconocidos[]}` — imports a `.docx`/`.odt` as a template body (RF-PRO-07, parity with `import_docx_editor.php`). Requires `USUA_PERM_ADMIN`. **It stores nothing**: it returns the body for review before creating the template, and with it the markers that would reject the save — said before, not after.
  **Declared best effort**: it preserves paragraphs, bold, lists and tables from `.docx`; from `.odt` only the text, paragraph by paragraph. `perdido[]` states what did not make it — headers and footers are **deliberately not imported**: the system's page template supplies them (RF-PRO-04), and carrying them over would duplicate the letterhead on every letter. An import that promises full fidelity and does not deliver it is worse than one that warns.
  Both the file size **and the uncompressed size** are bounded: a few KB compressed can expand to gigabytes, and that second defence is the one that matters. Binary `.doc` and `.rtf` are not accepted (they would require LibreOffice, decision D-1).
- `GET /api/v1/plantillas/campos` — **merge vocabulary**: the supported markers with `nombre`, `marcador` (`*NOM_R*`), `alias` (`{{nom_r}}`), `descripcion`, `grupo` and `diferido`. Requires `PERM_RADI`. The two `diferido` fields (`RAD_S`, `F_RAD_S`) do not exist until the number is consumed and are resolved in a second pass at registration time.
- `POST /api/v1/plantillas/{id}/combinar` `{radicado_padre_id?, dest_dept_code?}` — returns `{cuerpo, faltantes[]}`: the template body with the fields **already resolved on the server** and the list of markers left without a value. Requires `PERM_RADI`. **No-read-up (RF-SEG-08):** the parent radicado is read with the caller's clearance; if it exceeds it, its fields come back in `faltantes` exactly as if it did not exist — without that gate, merging would be an exfiltration path, because the parent's subject ends up written inside the body.
- `POST /api/v1/borradores` `{doc_type, subject, dest_dept?, cuerpo?, plantilla_id?, radicado_padre_id?}` — creates a draft. With `plantilla_id`, the template body is **merged** server-side against the context (parent radicado, sender, producing dependencia, dates); the fields left without a value are stored on the draft and returned as `faltantes[]`. The client never supplies that list: it ends up printed on a document of record.
- `GET /api/v1/borradores` (`?estado=`), `GET /{id}`, `PATCH /{id}` (edit only if not yet registered). **No-read-up (RF-SEG-08)**: a draft **inherits `nivel_seguridad`** from its parent radicado (no parent: 1; `BorradorResponse.nivel_seguridad`). List, read, edit, approve, return, preview and register filter by level in the `WHERE` (effective level = the greater of the inherited one and the parent's current one, so reclassifying the parent also closes its drafts); neutral `404` when out of reach. Creating over an unreachable parent gives `404` (it used to be `201` with empty context). `PATCH` writes a `borrador.editado` entry in the same transaction.
  **Who may EDIT (`PATCH /{id}`, AREA-01-N2, 2026-10-02)**: `PERM_RADI` (the route gate) is **no longer enough**; the caller must also **hold the radicado**. If the draft answers a parent radicado, whoever holds it per ADR-028 tenure edits it (the active flow step assigned to the actor, or unassigned and belonging to one of their dependencias); with no active step, whoever held the last completed step; with no step at all, whoever registered the parent (R16). With no parent, its creator. Anyone else gets the same neutral `404` as for a non-existent draft (the row is untouched), and the rule sits in the `WHERE` of the `UPDATE`. **`aprobar` and `devolver` do NOT require tenure** on purpose: they are review acts (approval is usually someone else's and is bound to the signature circuit); they keep `PERM_RADI` + clearance + state + circuit. **`radicar` DOES require the same tenure** (2026-10 audit, AGN Agreement 060/2001): it is the irreversible act —it consumes an immutable number—, so whoever does not hold the draft gets the neutral `404`, without consuming a number or touching the row (tenure sits in the `WHERE` of the read). And the `PATCH` `UPDATE` carries `estado <> 'radicado'`: if the draft is registered between the guard and the write, it answers `409 borrador_radicado` and does not edit.
- `POST /api/v1/borradores/{id}/previsualizar` → **`application/pdf`** with an `X-Faltantes` header (comma-separated): the fields **stored** when the template was merged plus any still unresolved in the current text. Without the stored ones this header always came back empty — the body is persisted already merged, so the gaps are empty strings and there are no markers left to report. It merges and renders **without persisting anything**: this is what lets you see the document before spending a number on it.
- `POST /api/v1/borradores/{id}/radicar` `{tipo_documental?, origin_dept_code?, dest_dept_code?}` (**optional** body, T13 / AREA-09-02) — the draft stores neither type nor departments, and the radicado requires them (AGN 060/2001 §5.2): whatever is not sent is **derived** (type from the template; destination from the name in `dest_dept` among active departments; a Salida's origin = the issuer's department: the one that received the parent if it is theirs, otherwise the lowest code among their active URDs) and whatever is sent **wins**. Nothing derivable → `422 datos_minimos_incompletos` with the missing `campos`, **before** the number is consumed; a removed department → `400 invalid_dependencia`. Generates the real radicado and marks the draft `radicado`. **Inherited level (T3):** the new radicado takes the parent's **current** level (the greater of the draft's snapshot and the parent's), its legal basis, cause and reservation term —if the parent has no legal basis it is justified by inheritance: it never falls to PUBLIC— and the `responde_a` link. `aprobar` (by a person, by a completed circuit and through the internal route) and `devolver` write the `borrador.aprobado` / `borrador.devuelto` entries in the same transaction as the state change. **An open circuit blocks here too** (RF-PRO-09): `409 circuito_pendiente` if any turn is unresolved, and the gate runs **before** merging, rendering and drawing the consecutive number. Putting the gate only on `/aprobar` was not enough: the circuit is opened over an **already approved** draft — that is the order of the procedure — so the real way around it was to register, not to approve twice. And registering is worse: approving twice is undone by sending the draft back to editing; a consumed number is never returned.
- `POST /api/v1/borradores/{id}/devolver` → sends an **approved** draft back to editing (`approved → draft`). `409` if it is already registered or was not approved. Without this return path the cycle ran one way only and the text froze on approval, so a signature circuit could not be reverted over changes to the document. **It does not touch the circuit**: reopening invalidates nothing by itself — sending back and re-approving without touching a letter leaves the approvals intact. What invalidates them is editing the text, because each turn is anchored to the body's digest.
- `POST /api/v1/borradores/{id}/aprobar` (`draft → approved`) — **the approval circuit binds** (RF-PRO-09). Before setting the state, document-service asks signature-service whether an approval circuit is open on this draft (`GET /api/v1/signature/internal/circuitos/{tipo}/{id}`, sealed with `X-Internal-Token`); if one is, `409 circuito_pendiente`. The gate is **binding if present, not mandatory**: with no open circuit, approval works exactly as before. `503 circuito_no_verificable` if signature-service is unreachable — and the draft is **not** approved: assuming "no circuit" would reopen the hole through the back door, precisely while the system is degraded. When the last turn is resolved, signature-service approves the draft itself (`POST /api/v1/internal/documentos/borradores/{id}/aprobar`, idempotent); that notice is **best-effort by design** and can be, because the authority is the query, not the notice.
- `POST /api/v1/signature/internal/circuitos/{objeto_tipo}/{objeto_id}/invalidar` (signature-service, `X-Internal-Token`) — voids the live circuits of an object **whose text changed**. document-service calls it when a draft already submitted for signature is edited. **Redoing a signed document is the procedure, not an abuse**: approvals are collected, someone remembers an annex or corrects a figure, and it has to start over; what cannot stand are the earlier signatures, given on a different text.
  **The hard guarantee does not live here**: it is the digest comparison at registration (`documento_modificado`), which refuses even if this notice is lost. This is what keeps the procedure **humane**: without it, the turns of a superseded circuit stayed in their holder's tray asking them to sign a document that no longer existed, and disappeared later with no explanation — "it vanished" for no reason is what makes people distrust the tool. Hence **best-effort** and **idempotent**: on an object with no live circuits it does nothing and returns `{invalidadas: 0}`. It looks up **by object, not by the type the caller declares** — the UUID identifies on its own, and filtering by both left a chain declared under another label on the same draft invisible.

### Quick response (legacy parity)

- `POST /api/v1/documents/{id}/respuesta` `{subject?, dest_dept?, dest_dept_code?, origin_dept_code?, doc_class?, observations?, response_days?}` — (T13 / AREA-09-02: a Salida requires origin and type; if the body omits them it **inherits** from the antecedent `origin_dept_code = the Entrada's dest_dept_code` and `doc_class`; nothing inheritable → `422 datos_minimos_incompletos`; an inherited department that has since been deactivated → `400 invalid_dependencia`, not `500`) creates a **Salida** (outbound) radicado in response to this one (inherits the `RE: …` subject and dependencia if omitted), linked to the antecedent (`responde_a`). Returns the new radicado.
- `GET /api/v1/documents/{id}/respuestas` — outbound documents that respond to this radicado. **No-read-up (RF-SEG-08):** the caller's clearance scopes which outbound documents are listed (_fail-closed_ to PUBLIC without identity) and each item carries `nivel_seguridad`, so the assistant's residency barrier strips the subject at levels 2-3.

### Two-step radicado annulment (legacy parity)

The law prohibits deleting radicados: they are annulled with supervisory approval.

- `POST /api/v1/documents/{id}/anulacion` `{causal, motivo?}` — requests (step 1). `409` if it is already annulled or there is a pending request.
- `POST /api/v1/documents/{id}/anulacion/aprobar` `{observacion?}` — approves (step 2): the radicado moves to `anulado` (the number is kept) and **its active flow step becomes `cancelled`** in the same transaction (`flow_events.tipo_tx=anulacion` event; without it the step stayed as a zombie in the inbox and overdue lists).
- `POST /api/v1/documents/{id}/anulacion/rechazar` `{observacion?}` — rejects. `GET /api/v1/documents/{id}/anulacion` — last request.
- `GET /api/v1/anulaciones?page&size` — **approver queue**: requests in state `solicitada`, oldest first (FIFO — a pending annulment blocks the document's lifecycle). Requires `PERM_PANU_CODI`, the **same** permission as deciding. Each row carries the request plus the radicado's `tracking_number`, `subject`, `doc_type`, `radicado_status`. `X-Total-Count` header. **No-read-up (RF-SEG-08) applied to rows AND to the count**: an unfiltered count would reveal how many requests exist over radicados the caller cannot see.

**Public single-window intake** (unauthenticated):

- `GET /api/v1/public/{tenant}/ventanilla/politica` — (B2, ADR-031) **the entity's current data-processing policy**: `{publicada, version, url, texto, huella}`. The policy is an entity parameter (`politica_tratamiento_version`/`_url`/`_texto`, tenant migration `012`, editable via `PUT /config/params/{key}`, level-3 administrator). `url` is only returned if it is `http(s)://` (the `PUT` of `politica_tratamiento_url` rejects any other scheme with `422 url_invalida`). An unknown tenant answers `publicada=false` like `/estado`. `publicada=false` when the version is missing, or there is a version but neither URL nor text (a notice that points nowhere does not inform). `huella = sha256(version ␞ texto ␞ url)` is computed by the **server**. **Strict read**: if tenant-service does not answer, **`503 politica_no_verificable`** (an error is not "no policy").
- `GET /api/v1/public/{tenant}/ventanilla/estado` — (T13 / AREA-09-07) **pre-flight check**: `{habilitada, dependencia_receptora, tipos_admitidos, politica_publicada}`. `politica_publicada` (B2) is **not** part of `habilitada` (org chart + catalogue); with no policy `POST` answers 409. `503 politica_no_verificable` if tenant-service does not answer. `habilitada` is the SAME condition `radicar` applies internally (an active department with `depe_rad_tp2` **and** at least one admitted type), computed without creating anything; an unknown tenant answers `habilitada=false`. `scripts/verificar-ventanilla.sh` walks it per entity and exits non-zero if any is not enabled **or has no published policy** (to hook into deployment).
- `GET /api/v1/public/{tenant}/ventanilla/tipos` — document types the entity declares it accepts (`tipos_documentales.admite_ventanilla_publica`, **opt-in**). Does not expose the internal catalogue.
- `POST /api/v1/public/{tenant}/ventanilla` — external intake. The type must be declared admissible (**422 `tipo_no_admitido_en_ventanilla`** otherwise; never silently reclassified). Routed to the dependency flagged `depe_rad_tp2` ("web intake enabled"); if none is declared, **503 `ventanilla_no_habilitada`** — entering without a destination would leave the document in no one's inbox. The sender may be a person **or an organisation** (`sender_entity`). Identified senders must give a reply channel; **anonymous** intake is legitimate. Returns tracking number, verification code and destination dependency.
  **Multipart**: fields go in the `datos` part (JSON), attachments in `anexos` parts. One door for both the simple case and a writ with its evidence. Limits: **5 files, 10 MB each, 25 MB total**; PDF, JPG, PNG and TIFF only, **verified by content** — not by extension or `Content-Type`, which the client writes — and the stored MIME is the sniffed one. **No antivirus.** Attachments are stored BEFORE a tracking number is assigned: if storage fails, **503 `anexo_no_almacenado`** and nothing is filed, because a tracking number is immutable and must not exist without its evidence. The file is stored with no actor (`uploaded_by = NULL`).
- **Personal-data authorisation, honeypot and idempotency of public intake (T13 / AREA-09-08, AREA-02-07).** `POST /ventanilla` and `POST /pqrs` require `acepta_tratamiento: true` (`Literal[True]`: there is no valid "false" → `422`) and `politica_version` (1-50 chars): **the version the client displayed** (the one from `GET /ventanilla/politica`). **The server re-reads the current policy** (B2, ADR-031): no policy published → **`409 politica_no_publicada`** (fail-closed: no privacy notice, no informed authorization, Law 1581 art. 12); `politica_version` different from the current one → **`409 politica_desactualizada`** (the body carries `politica_vigente`); tenant-service down → **`503 politica_no_verificable`**. Everything is validated **before** uploading attachments, claiming the `Idempotency-Key` and consuming the sequence number (a 409/503 leaves no trace); an idempotent retry of an already-filed radicado returns what was filed without re-reading the policy. The server persists **record v2** in `metadata.consentimiento = {norma: "Ley 1581/2012", version_constancia: 2, politica_version, politica_huella, politica_url, instante, ip, ip_verificada}` **and** in the payload of the `document.radicado_created` audit entry (the one with evidentiary value). **Version, hash and URL come from the server**: whatever the client sends as hash or URL is ignored. **`ip` is the one the gateway SIGNED** (`X-Real-Ip` + `X-Real-Ip-Ts` + `X-Public-Ip-Signature`, HMAC with `IDENTITY_SIGNING_SECRET` bound to the route and valid for 120 s; the gateway derives it through trusted hops with `TRUSTED_PROXIES`, and document-service **never** reads `X-Forwarded-For`). Without a valid signature (direct call, expired or other-route signature, no secret) → `ip = null`, `ip_verificada = false`, and **filing is not blocked**: the IP is complementary evidence, not a requirement of the authorization. v1 records (`ip_declarada`) are kept as they were. A **honeypot** field `sitio_web` (invisible in the form) must arrive empty: with content → generic `422`, validated in the schema, before resolving the type, the department or consuming a number. The optional **`Idempotency-Key`** header (16-128 chars `[A-Za-z0-9_-]`; malformed → `422 idempotency_key_invalida`, never silently ignored) makes a retry return the **same** radicado: the key is claimed (`public_submission_keys`, migration `043`) with `ON CONFLICT DO NOTHING` **inside the radicado's transaction and before the sequence number**, so two simultaneous submissions serialise on the unique index (the second waits for the commit and returns the existing one, `201`, same number, a single sequence number spent). The same key with **different content** → `409 idempotency_key_reutilizada`. The CAPTCHA provider (reCAPTCHA contradicts the declared sovereignty; the self-hosted alternative is an ALTCHA-style proof of work) and the policy text remain the entity's decision.
- `POST /api/v1/public/{tenant}/pqrs` — **legacy**, kept because it is an already-published public surface; delegates to the single window and is not extended.

### Electronic signature (E06, ADR-016)

- `POST /api/v1/documents/{id}/signatures` `{ "anexo_id", "reason"? }` — **declarative** signature of an attachment (simple electronic signature; evidentiary signatures live in signature-service): records identity (gateway claims), hash and timestamp. **Requires `PERM_FIRMA` level >= 2** (write). **The server resolves the hash** from the SHA-256 storage computed at upload: a body `content_hash`, if sent, **is ignored** (a client-chosen hash proves nothing). `422 anexo_requerido` without `anexo_id`; neutral `404` if the radicado does not exist, exceeds the clearance or the attachment is not its own; `503 huella_no_verificable` if storage is down; `409 anexo_sin_huella`. The record and the `document.signature_created` entry in `public.audit_log` are **one act** (same transaction). *Pending a user decision: retire the endpoint with `410` (it has no consumer).* **All three signature routes** require `PERM_FIRMA` (the same permission that governs signature-service) and apply no-read-up: neutral `404` if the radicado does not exist or exceeds the clearance.
- `GET /api/v1/documents/{id}/signatures` — lists the signatures (`X-Total-Count`). `404` if the radicado is not visible; `[]` (200) if it is visible and has no signatures yet — a sub-clearance caller always gets `404`, never `[]`, so the pair is not an oracle.
- `GET /api/v1/documents/{id}/signatures/verify?content_hash=<hex>` — `{content_hash, valid, signatures[]}`: `valid=true` if any signature of the radicado matches that hash (if the content changed, it stops verifying). Gated like the listing: without the filter, the true/false `valid` would be a **content oracle** over reserved documents. Only signatures with `origen='servidor'` count: legacy ones with a client-declared hash (`cliente_no_verificado`, migration 036) do not verify.

### Postal dispatches (E20)

- `POST /api/v1/documents/{id}/envios` `{operador, destinatario?, direccion?}` — dispatches the radicado via a postal operator (4-72/servientrega/otro); generates the tracking guide and `registrado` status.
- `GET /api/v1/documents/{id}/envios` · `GET /api/v1/envios/{id}` — dispatches of the radicado / detail with event history.
- `POST /api/v1/envios/{id}/estado` `{estado, descripcion?}` — updates the delivery status (operator callback/polling). State machine `registrado→en_transito→entregado|devuelto|fallido`; `409` if the transition is invalid.

**Mail accounts and OAuth (E19).**

- `POST|GET /api/v1/email/apps-oauth` — the **OAuth application the INSTITUTION registered** in its own Google Cloud or Azure AD. `{proveedor, client_id, client_secret, azure_directory_id?, redirect_uri, notas?}`; one row per provider (`409 app_ya_registrada`), and the same one serves every account of that provider. Microsoft requires `azure_directory_id`; Google does not use it (`422` either way). The `client_secret` is stored encrypted and never returned.

  > **Why the institution registers it and not the deployment.** Google requires verification and an annual third-party **CASA security assessment** for any application requesting the `https://mail.google.com/` scope (the one covering IMAP), but **exempts internal ones**: apps used only by people inside the organisation's own Workspace or Cloud Identity. A single shared application across deployments would be external to every institution and would drag all of them into that assessment.

- `POST|GET /api/v1/email/cuentas` — mailboxes. **The two verbs do not ask for the same thing**: creating requires `USUA_PERM_ADMIN`, listing only `PERM_RADI` — the same permission as the inbox, because it is the only way a correspondence officer can know which mailboxes exist and under which identifier; with an admin gate, the screen the inbox points at was unreachable for its own audience.

  A **personal account's credential is not stored at all** — it is asked for at sign-in and lives in the session; custodying it would make the system the holder of an officer's entire mail. Institutional ones are encrypted with AESGCM and AAD `tenant|cuenta`. Creating a `gmail` or `microsoft` account with a password is rejected with the reason: those platforms no longer accept one over IMAP.

  **Visibility**: a personal account is seen only by its owner — **not even by the administrator** — and an institutional one by its assigned officers plus the administrator. The narrowing lives in the `WHERE`, and `404` does not distinguish "does not exist" from "you cannot reach it".

- `POST /api/v1/email/cuentas/{id}/probar` `{password?}` — opens a session and **returns the mailbox folders**, without touching any message. The folders are half the value: the filtering folder is picked from that list instead of typed by hand, and a typo would show up as "ingestion brings nothing", indistinguishable from "no mail has arrived". `password` only for personal accounts.

- `POST /api/v1/email/cuentas/{id}/oauth/iniciar` — returns `{authorization_url}`, the provider URL to send the administrator to. It carries **PKCE S256** (RFC 7636) and a single-use `state` (RFC 6749 §10.12), plus `login_hint` with the mailbox address: without it, someone with several accounts open authorises with whichever the provider picks and the token ends up issued for the wrong mailbox.

- `POST /api/v1/email/cuentas/{id}/oauth/canjear` `{code, state}` — closes the flow and stores the encrypted refresh token. The `state` is validated and marked used **in the same `UPDATE`**, under four conditions (single use, not expired, same account, same initiator), so a replayed redirect finds no row. `400 state_invalido` does not say which of the four failed.

  Scopes: Google `https://mail.google.com/`; Microsoft `https://outlook.office.com/IMAP.AccessAsUser.All` **plus** `offline_access` — not `outlook.office365.com`, which belongs to the application-credentials flow and yields a token the IMAP server rejects.

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

Statistical summary of the tenant's radicados (E09): `total` and counts `by_doc_type`, `by_status`, `by_month` (`YYYY-MM`), `by_dependencia` (the **registration** dependencia: who it was addressed to, counts every radicado) and `by_ubicacion` (**ADR-029**: where what is still in process is NOW, **excluding** closed and annulled: each dependencia's real workload). Optional filters `?date_from=&date_to=`. Native SQL aggregations (`GROUP BY`/`date_trunc`).

### `GET /api/v1/reports/radicados.csv`

Exports the summary in **CSV** (`seccion,clave,valor` with type/status/month/`dependencia_radicacion`/`ubicacion_actual` sections; the `dependencia` section was renamed `dependencia_radicacion` as ambiguous — ADR-029 —) — for periodic reports to the AGN/oversight bodies. Same filters `?date_from=&date_to=`.

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

**Complete index and "never NULL" (T13 / AREA-09-04, migration `041`).** Each row adds `dependencia` (the department that **receives** E/I or **produces** S) and `registrado_por` (opaque user identifier; `null` for what came in without a user). The promise that no classified radicado lacks a legal basis is now held by the database: `CHECK radicados_clasificado_con_fundamento` (`NOT VALID` + backfill with the explicit text "PENDIENTE DE MOTIVAR…" for legacy rows, validated inside the migration) and the INT-05 import rejects the whole package with `422` if any radicado carries `nivel_seguridad ≥ 2` without `clasificacion.fundamento`. The series/file level and the closed catalogue of legal bases remain pending (entity decision).

### `GET /api/v1/search`

Advanced radicado search (E09, ADR-014: PostgreSQL FTS). Full text with ranking (`tsvector`/`ts_rank`) + fuzzy (`pg_trgm`), combinable with structured filters.

Searches in: `tracking_number` (weight A), `subject` (B), `sender_name` / `sender_entity` (C), `dest_dept` / `observations` (D).

**Query params** (all optional): `q` (text, min 2 chars — **optional**: without text it is a filtered query ordered by date), `doc_type`, `status`, `date_from`/`date_to` (range over `registered_at`), `dest_dept_code` (**registration** dependencia), `ubicacion_dept_code` (where it is **now**, ADR-029; same `WHERE` in rows and `COUNT`), `meta.<campo>=valor` (metadata filter, JSONB/GIN containment), `page`, `size`. Header `X-Total-Count`. Each result carries `ubicacion_dept_code`/`ubicacion_dept` besides `dest_dept`.

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


## Dead-letter queue — admin (ADR-021 phase 5)

Three services keep their own dead-letter queue for events they could not consume: `signature`, `workflow` and `notification` (**singular** — the path is `/api/v1/notification/...`, not `notifications`; the Spanish page said otherwise until 2026-08-29 and a mechanical route census caught it).

| Method | Route | Notes |
|---|---|---|
| `GET` | `/api/v1/{signature\| Paginated list (`X-Total-Count`). Headers only (no business payload), **narrowed by the caller's clearance inside the `WHERE`** (LIVE level of the referenced radicado; doubt = level 3): a row above the caller's level neither appears nor counts, and its detail/replay/discard answers the same `404` as an unknown `event_id`. Exact page and total, no candidate cap; the `X-Truncated` header was retired. `503` if `radicados` is missing. |notification\|knowledge}/admin/deadletter?status=pending&origin_stream=&page=&size=` | Paginated list (`X-Total-Count`). Headers only (no business payload) and **narrowed by the caller's clearance** — row-by-row no-read-up against the *live* level of the referenced radicado. A row whose object exceeds the admin's level is omitted entirely: it neither appears nor counts towards `X-Total-Count`, consistent with the neutral `404` of the detail view. Fail-closed on any resolution error. The candidate set is capped at `1000` before filtering; reaching the cap adds **`X-Truncated: true`** (warning that `X-Total-Count` may under-report). |
| `GET` | `/api/v1/{signature\|workflow\|notification\|knowledge}/admin/deadletter/{event_id}` | Detail (full envelope + failure). Clearance-gated; **neutral `404`** — byte-identical body — when insufficient or non-existent. An authorised view writes an `evento.dead_letter.viewed` entry; **a blocked view does not audit**, so as not to confirm existence. |
| `POST` | `/api/v1/{signature\|workflow\|notification\|knowledge}/admin/deadletter/{event_id}/replay` | Re-injects the event into its stream. Same clearance gate, audited. |
| `POST` | `/api/v1/{signature\|workflow\|notification\|knowledge}/admin/deadletter/{event_id}/discard` | Discards it permanently, with a reason. Same gate, audited. |

Since batch T9 (AREA-10-07/10-10) the dead-letter surface also exists in **knowledge-service** (`/api/v1/knowledge/admin/deadletter`; `replay`/`discard` need level 3; replay runs the ingestion handler in the same transaction as the state transition and the audit entry; an event type knowledge does not apply returns `422 unsupported_event_type`). `evento_dead_letter` is one physical table shared by the four consumers; its primary key is **`(origin_group, event_id)`**, so the same event parked by two groups leaves one row and one `evento.dead_letter` entry per group (repeating the same pair stays idempotent). `X-Truncated: true` is sent only when MORE than `1000` candidates exist (the repository asks for `1001`). In services with an event consumer `/health/ready` adds `checks.dead_letter` (`ok`/`degraded`) and a `consumer` object (`dead_letter_stream_len`, last sweep counters); it reports only and never decides the `503`.

Gate: `PERM_DLQ_ADMIN` (**not** `USUA_PERM_ADMIN`) on all four.

---

## Access history — who read and who downloaded (RF-BUS-10)

`GET /api/v1/expedientes/{id}/historial-acceso` — gate `USUA_PERM_EXPEDIENTE` plus **no-read-up**: an expediente above the caller's clearance answers `404`, not `403`, before touching `audit_log`.

**Extended on 2026-08-29 to cover accesses to the expediente's DOCUMENTS.** Until then the query filtered only by the **expediente code**, while the two document-access traces —`document.radicado_consultado` and `document.anexo_descargado`, written by document-service with actor, channel and security level— use `object_type='radicado'` and `object_ref=<tracking number>`. So **"who downloaded this expediente's documents?" had no answer on the screen built to answer it, even though the data had been stored all along.**

The admitted radicado actions are an **admission list, reads only**: a radicado also accumulates creation, annulment and reclassification, and dumping those here would turn an *access* history into a history of everything.

It opens no leak: those tracking numbers are already listed in the expediente view for anyone past its gate, and each entry returns **actor, action, timestamp and reference** — the `payload`, which carries the document's classification level, is **not rendered**.

---

## Third parties and communication actors (E01, RF-RAD-05) — document-service

`GET /api/v1/terceros?q=&tipo=&limit=` · `POST /api/v1/terceros` · `GET /api/v1/terceros/radicado/{id}/actores` · `POST /api/v1/terceros/radicado/{id}/actores` · `GET /api/v1/terceros/divipola/departamentos` · `GET /api/v1/terceros/divipola/municipios?departamento=&q=` · `GET /api/v1/terceros/duplicados?limit=` · `GET /api/v1/terceros/{id}/radicados?limit=` · `POST /api/v1/terceros/{id}/fusionar` — gate `PERM_RADI` on the `APIRouter` itself (`crud≥3` to link actors).

**Actors travel with the radicación.** `POST /api/v1/documents` accepts `actores: [{tercero_id, rol, principal?, canal?, …contact}]` and freezes them **inside the very transaction** that creates the radicado and its audit entry: a non-existent `tercero_id` rolls the whole radicación back (404) instead of leaving a radicado with no sender and the number already consumed. `POST /terceros/radicado/{id}/actores` remains for adding them later. Until 2026-09 no screen called either of them and `radicado_actor` was **empty** across 182 radicados — the directory learned from nobody.

**Directory administration** (`/admin/terceros`):

- `GET /terceros/duplicados` — live records that are **probably** the same person, along **two axes that are not worth the same**: `documento` is certainty (same ID number = same person) and `nombre` — normalised without accents or case — is **suspicion**, because namesakes exist. It only reports; the system never merges on its own. It excludes already-merged records, or the pending list would never shrink.
- `GET /terceros/{id}/radicados` — where the record appears and in what role. **Includes those of records merged into it**: without that carry-over, consolidating two records would make the absorbed one's radicados vanish from view. **No-read-up (RF-SEG-08)**: only radicados whose `nivel_seguridad` the caller's clearance reaches are returned (each item carries `nivel_seguridad`; the assistant's residency barrier strips the subject at 2-3); the read leaves a `document.tercero_radicados_consultado` entry.
- `POST /terceros/{id}/fusionar` `{absorbe_id}` — consolidates a duplicate; the one in the path **survives**. Requires `PERM_RADI` with **`min_crud=3`**. **It rewrites nothing**: it marks the absorbed record with `fusionado_en` and nothing else, because `radicado_actor` is immutable by trigger (migration 026) **including its `tercero_id`** — which catalogue record someone was served under is also part of what happened, since two of them existed that day. The merge adds what was learned afterwards and resolution happens on read. Returns `vinculos_heredados`. Errors: `422 fusion_consigo_mismo`, `404 tercero_no_encontrado`, `409 tercero_ya_fusionado` (merges do not chain: a chain with a cycle would hang the reader) and **`409 documentos_distintos`** when both records carry an ID number — `ux_tercero_documento` is UNIQUE among live records, so two different numbers are two people and consolidating them would destroy a legitimate identity; if one was mistyped, fix the number first and merge after. The message names both numbers. Audit entry `document.tercero_fusionado`, atomic and carrying **both names** — the absorbed record stops appearing in listings, and a bare id could not be resolved later.

**The rule that governs the model: *identity by reference, contact data by frozen copy*.** It comes from two real problems in the Orfeo system this one derives from:

1. **The same person could appear thirty times** in the catalogue, with different addresses and variations in accents or spelling. The cause was not a missing uniqueness constraint: it was **tying identity to contact data**, so every new address forced a new record.
2. **The address a letter was actually sent to cannot change afterwards.** If the record were updated, every past dispatch would claim it went somewhere else — wrong information about an act performed with a datum that **was true at the time**.

Hence the model (migration `026`): `tercero` holds **who** —with the requirement's five typologies and an identity document— and `radicado_actor` holds **where it was sent**, with name, entity, email, phone and address **frozen** and **immutable by trigger**. `tercero_id` travels on the link so "what have we sent to this person?" stays answerable even as the record changes over the years.

**Three defences against duplication, all three needed**: a unique index on the identity document; **accent- and case-insensitive search** (duplicates are not created by bypassing an index, they are created by **not finding what is already there**); and uniqueness by **identity + destination** — the same person at two different addresses is a legitimate dispatch, the same person at the same address twice is a duplicate. Re-adding is a **no-op**, not an error.

**Contact data can be corrected for that dispatch** without touching the catalogue: when registering, you look someone up and adjust their email or address, because that happens. That adjustment is what gets frozen; updating the record is a separate, explicit act that drags nothing backwards. The audit entry notes **how many actors were frozen with contact data differing from the record**.

**The dispatch channel is a material condition, not a preference.** A recipient is served by email, physically, or both, and **each channel requires its datum**: no email, no electronic dispatch; no address, no physical dispatch. Two `CHECK`s enforce it — **an impossible dispatch cannot even be recorded** — because recording it would have the radicado promise something unfulfillable, with the failure surfacing at dispatch time, when there is no longer anyone to ask.

`canal = NULL` means **not decided yet**, admitted on purpose: forcing the choice at link time would push people to pick any channel just to move on. It can be set later, and only if the datum is there.

The check runs against the **frozen copy**, not the catalogue record: dispatching by a datum that does not travel in the radicado would mean sending somewhere the radicado does not state. The `422` **names the recipient and the missing datum**.

### Geography is CHOSEN, not typed — the DIVIPOLA cascade (2026-08-30)

The three defences above tackle duplication of the **subject**. The other half stayed open, at the **destination**: while `municipio` and `departamento` were free text, "Bogotá", "BOGOTA", "Bogota D.C." and "Santa Fe de Bogotá" were **four different municipalities** to every query, report and export. A uniqueness constraint does not close that, because the four spellings ARE different and all of them pass. The only thing that closes it is the municipality no longer being something you type.

`GET /terceros/divipola/departamentos` returns the **33 departments**; `GET /terceros/divipola/municipios?departamento=05` returns that department's municipalities, with an optional `q` that **ignores accents** (typing "monteria" finds "Montería"). The department is mandatory for municipalities: all 1,135 at once would be the list nobody scrolls. Both inherit the router's `PERM_RADI` gate.

The catalogue (**global** migrations `003` + `004`, `public` schema) is **national**: the institution does not edit it, it does not vary between institutions, and replicating it per schema would copy data nobody can diverge. It holds **1,122 current entities** —1,103 municipalities, 18 non-municipalised areas and 1 island— taken from DANE's **official source** (Colombian Open Data, resource `gdxc-w37w`, retrieved 2026-08-30). Updating it is an `INSERT ... ON CONFLICT` over the same tables, which is why the load is idempotent. A suppressed municipality is **not deleted** —historical dispatches name it— but flagged `vigente = false`, dropping out of the cascade while still resolving a past dispatch's name.

> **`003` loaded the catalogue from the source Orfeo dump, and it was not DANE's.** It was checked against four known cases, all four were right, and the rest was not checked: it hid **38 differences**, among them **Pasto attached to Antioquia** under the code belonging to Alejandría, 18 codes DIVIPOLA does not recognise, and 5 missing municipalities —Tuchín and Nuevo Belén de Bajirá, the latter added by DANE in 2023—. `004` corrects it from the official source; **`003` was not rewritten**, because the `schema_migrations` ledger identifies by file name and stores no content hash, so a deployment that had already applied it would have silently kept the bad catalogue.

**Two guarantees against silent incoherence** (migration `027`): the municipality code **contains** its department's, and a `CHECK (left(codigo,2) = departamento_codigo)` enforces it — the error a badly written cascade produces without warning; and the department is **derived** from the municipality in the service layer rather than accepted from the client, so a contradiction cannot arrive in the first place.

And **a deliberate asymmetry between the two tables**: in `tercero` the codes carry foreign keys to the catalogue — a live record must point at a municipality that exists; in `radicado_actor` they **do not**. That row is the frozen copy of where something was sent and has to survive the catalogue changing, including the municipality ceasing to exist: tying it would turn a territorial reorganisation into a corruption of the historical record. Same reason contact data is copied rather than read by reference.

The stored name becomes the catalogue's **official** one rather than what was typed, so search, reports, the electronic index and export —which still read the text columns— stop seeing four spellings of the same municipality without any of them changing. The code is **optional**: an address abroad has no DIVIPOLA and records predating the catalogue remain valid — requiring it would have closed the mail room over a presentation detail. For the same reason the municipality joined the identity+destination deduplication: "Calle 10 #5-20" exists in half the country, and without it two legitimate dispatches to that address in two municipalities were rejected as duplicates.

> **`sender_name` and `sender_entity` are untouched**: forty-three files read them, so the new model is **additive**.

---

## Format migration against obsolescence (E10, RF-PRE-04)

`POST /api/v1/preservacion/migrar-formato` · `GET /api/v1/preservacion/migraciones/{file_id}` — gate `USUA_PERM_EXPEDIENTE` `crud≥3`.

The rule (Decree 2609/2012 art. 32) asks for three things, and all three are invariants of the endpoint:

1. **Keep the original.** The migration produces a **derived** artifact that lives alongside it; the source file is not touched, not flagged, not replaced. A system that replaces the original has lost the evidence the day someone disputes whether the conversion altered the content.
2. **Record source and target versions** with their provenance chain (`preservacion_migracion`, migration `015`) and their PREMIS **`MIGRACION`** event — a type that had been in the vocabulary from the start and that **nobody wrote** until now.
3. **Verify integrity before and after.** The source SHA-256 is **recomputed** over the downloaded bytes and compared with the stored one **before** converting: migrating an already-corrupt file would propagate the corruption into the preservation copy and give it the appearance of being the good one. A mismatch answers `409` and **records the attempt** — a file that fails fixity is exactly the one to go and look at.

And a fourth the rule does not state and practice does: **converting to PDF/A without checking that the result IS PDF/A produces a copy nobody validated.** The derivative goes through veraPDF, with three outcomes that are never conflated: `migrado` (conformant), `fallido` (non-conformant — the bytes are discarded) and `no_verificado` (converted, but no validator available: **not promoted** to a preservation copy either).

> **`503` when the deployment has no converter, and there is no "stub migrator".** The PDF/A validator's twin factory degrades to an honest stub because "not evaluated" is a real result; here there is no equivalent — a stub would have to **produce a file**, and the only file it can produce without the tool is an invented one. Same deliberate asymmetry as voice transcription. The production image installs `ghostscript` and sets `FORMAT_MIGRATOR=ghostscript`.

The profile is limited to the **`b`** variants of PDF/A (`1b`/`2b`/`3b`, default `2b`): the `a`/`u` ones require tagged text or guaranteed Unicode, which an automatic conversion cannot promise. `GET /migraciones/{file_id}` returns the provenance chain **including failed attempts**: "it was tried and it did not work" is what tells you which documents are still waiting for a migration that never comes.

### The periodic sweep, and whose decision it is (2026-08-30)

Until now conversion could **only be triggered by hand, one file at a time**. An institutional archive holds hundreds of thousands of files: a capability you have to invoke one by one is a capability built and not offered. The sweep (`FORMAT_MIGRATION_ENABLED`, cadence `FORMAT_MIGRATION_INTERVAL_SECONDS`, daily by default) walks the active tenants and queues whatever is missing.

**Which format is obsolete is declared by the institution, not by this code.** That is an archival judgement depending on each institution's holdings and its PINAR (Ac. AGN 003/2015), so the job carries no list of its own and **with no policy declared it migrates nothing**.

Where it is declared, and in which order it is read:

1. **The institution's preservation plan** — `preservacion_plan.formatos_origen`, written from `/admin/preservacion` (`PUT /api/v1/preservacion/plan`). This is its proper home: append-only versioned, and it records who declared it and when.
2. **The deployment** — `FORMAT_MIGRATION_MIME_TYPES`, only if that institution's plan says nothing. It is an installation-wide default: in a single-institution install the deployer **is** the institution; in a shared one each institution overrides it by declaring its plan.

Never the other way round. Keeping the policy **only** in an environment variable contradicted the rule itself: an environment variable is the same for every institution on the installation, the archivist cannot touch it, it is not versioned and it does not say who set it. The MIME comparison is case-insensitive (RFC 2045 §5.1): the stored value is whatever the uploader sent, and an `Application/MSWord` left outside the policy would have yielded zero candidates with nothing appearing broken. With the policy undeclared the sweep walks the tenants and touches not a single file. Same criterion as the disposition sweep —which counts and warns but never deletes— with one difference worth keeping in mind: **migrating destroys nothing**; the original is kept intact and the derivative lives alongside it. The danger here is not converting too much, it is believing something was converted — which is why the sweep promotes nothing that did not pass the validator.

**`fallido` and `no_verificado` are not treated alike, and the distinction decides what gets retried**: `fallido` speaks about the **file** —it could not be converted, or the result was non-conformant— and is **not** retried, because retrying it nightly costs a subprocess per file and fixes nothing; someone has to look. `no_verificado` speaks about the **deployment** —it converted and there was no validator to check it— and **is** retried, so the day veraPDF is installed the next sweep resolves it on its own.

`FORMAT_MIGRATION_BATCH_SIZE` (50 by default) bounds each sweep: converting is expensive and without a cap the first one would eat the machine. What does not fit in one sweep fits in the next. A failure stays contained in its file and a failed sweep in its tenant; with no converter available the **whole** sweep stops at the first attempt rather than spending a download per file to reach the same `503`.

---

## Voice dictation (E18, ADR-020)

- `GET /api/v1/assistant/transcribe` — **availability probe**, not decoration: it answers whether a transcription provider is configured and reachable, so the UI can decide whether to offer the microphone instead of offering it and failing when the button is released.
- `POST /api/v1/assistant/transcribe` — takes the audio, returns the text.

**There is no `stub` provider, and the asymmetry with the rest of the system is deliberate**: a validator can honestly report "not evaluated", but a fake transcriber would have to **invent what was said**. With no provider the `POST` answers `503` and the `GET` says so beforehand.

---

## Other routes documented in the Spanish page

These exist and behave identically; the Spanish page (`docs/es/api.md`) carries the full rationale:

- `GET /api/v1/dependencias/tree` — the org chart as a **tree** (`parent_id` resolved), not a flat list: rebuilding the hierarchy client-side from a partial page produces orphan nodes.
- `GET /api/v1/documents/{id}/anexos/{anexo_id}/download` — annex download **mediated by document-service**, so storage-service stays sealed and no-read-up is decided by whoever knows the radicado's classification.
- `GET /api/v1/expedientes/{id}/aip` · `GET /api/v1/expedientes/{id}/preservacion/eventos` (archive-service) — the expediente's AIP and its PREMIS events, behind `USUA_PERM_EXPEDIENTE`. **AREA-07-15**: the events are no longer only those keyed by the expediente: the server resolves the `object_ref` set —the expediente, its AIP id and the `file_id` of the annexes of its **current radicados visible to the caller**— and asks storage for it, so per-file events (PDF/A validation, format migration) show up, and so does the **fixity check of the receipt of its transfer**. If document-service is down: `503 document_service_unavailable` (never a partial list presented as complete).
- `POST /api/v1/expedientes/{id}/aip/reintentar` (archive-service, AREA-07-14) — puts back to `pendiente` an AIP the job left **`agotado`** (the job only ever moved it to `agotado`; there was no way back). Needs `USUA_PERM_EXPEDIENTE` **crud ≥ 3**. A single `UPDATE` with the `agotado` state and the caller's clearance **in the `WHERE`**: on an `empaquetado`/`omitido`/`pendiente` AIP, or an expediente the caller cannot reach, the answer is a neutral `404 expediente_not_found`. `intentos` goes back to 0 and the next sweep packages it; `archive.aip_reintento_manual` audit entry (with `intentos_previos`/`error_previo`) in the same transaction. `200 {expediente_id, estado: "pendiente"}`.
- `GET /api/v1/reports/indice-reservado.csv` — CSV form of the classified-information index (Law 1712 art. 20), content-free.
- `GET /api/v1/public/archive/eliminacion/{tenant}/lotes/{lote_id}` — a published elimination batch, unauthenticated.

---
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
> above (see [Conversational assistant (E18, ADR-019/ADR-020) — via the gateway](#conversational-assistant-e18-adr-019adr-020-via-the-gateway)),
> with a real LLM loop executing the read-only tools _as-the-user_. This section
> is kept because it documents the other half of the contract — **what the
> frontend's SSR proxy does with the response** — which the backend section does
> not cover.
>
> The `503 assistant_unavailable` still exists, but it no longer means "the
> backend is not built": the mcp-server itself returns it when
> `ANTHROPIC_API_KEY` is missing or the SDK fails (honest degradation,
> RF-FIR-15). **Both causes share the code but not the message**, because they
> ask different things of the reader: with no model configured, `detail` says the
> installation lacks one and that whoever administers it can enable it — waiting
> does not fix it; with the provider down, it says to retry in a few minutes. The
> SvelteKit proxy **forwards** that reason to the panel (only from the assistant's
> own envelope, capped at 300 characters) and translates any other absent route
> (404/501/5xx/connection refused) into "the assistant service is not
> responding". Until 2026-10 the proxy replaced it with a fixed text and the UI
> promised the feature "will be enabled soon" — a leftover from when the backend
> did not exist.

### `POST /api/v1/assistant/message`

Sends one user turn and returns the assistant's reply. Authorization: **authenticated** (no fine-grained permission; every action the assistant triggers downstream is revalidated against the user's RBAC/clearance in the service that executes it). The identity headers (`X-Tenant-Slug`/`X-User-Id`/`X-Username`) are injected by the SSR proxy from `locals`, never from the body.

```json
// Request  (allow-list in the proxy: these two fields only)
{ "mensaje": "string (≤4000, trimmed)", "conversation_id": "uuid (optional)" }
// Response 200
{
  "conversation_id": "uuid",
  "message": { "id": "uuid", "role": "assistant", "texto": "reply (AUTHORITATIVE)", "formato": "markdown|text", "ts": "ISO-8601" },
  "propuestas": [],
  "no_verificados": []   // radicados in the text that no tool_result returned (AREA-11-04)
}
```

- `texto` is the **source of truth**; `formato` is a rendering hint. The proxy converts `formato:"markdown"` to HTML with `marked` and **sanitises it server-side** (`sanitizeHtml`) before it crosses to the client — the assistant's output is **untrusted content** (XSS/prompt injection). The backend must **not** send HTML; if it did, the proxy ignores it.
- Reserved for the future (the current slice ignores them without breaking): `acciones[]`, `citations[]`, `usage`.
- Ready for **streaming** (not built): a `POST .../message/stream` (NDJSON/SSE) with the same `conversation_id` and a `message.id` stable per turn would allow appending deltas to the same turn without changing the shape.
- **Contract note (to reconcile)**: the proxy sends `X-Username` from the `preferred_username` claim (the frontend's `SessionUser` has no `username`).
