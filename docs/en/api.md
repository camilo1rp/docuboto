# API Reference

> Base URL in development (from the host, via the gateway's published port): `http://localhost:19080/api/v1`
> All endpoints (except the public ones) require `Authorization: Bearer {token}`.
>
> The "port NNNN" noted in each section below is the **internal** container port
> inside the `orpycamcp-net` Docker network — never accessed directly from the host,
> always through the `api-gateway` (published port 19080). See the full
> internal↔published mapping in [Architecture](architecture.md#services-and-ports).

## Conventions

| Convention | Value |
|---|---|
| Authentication | `Authorization: Bearer {JWT}` |
| Pagination | `?page=1&size=20` — response includes `X-Total-Count` |
| Dates | ISO 8601 UTC — `2024-01-15T10:30:00Z` |
| IDs | UUID v4 |
| Multi-tenancy | Header `X-Tenant-Slug` injected by the gateway after validating the JWT |

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

### URD and dependencia context (E08)
- `GET /api/v1/auth/users/{user_id}/urd` · `POST` · `DELETE /{urd_id}` — manages the User-Role-Dependencia assignments (one per (user, dependencia); a single primary one). Requires `USUA_PERM_ADMIN`.
- `GET /api/v1/auth/context` — caller context: `{user_id, active_depe_id, available_depts[]}`.
- `POST /api/v1/auth/context/switch` `{ "depe_id": 120 }` — sets the active dependencia (organizational unit) for the user themselves. **Does not reissue the token** (ADR-013): it persists the context and audits it. `400 no_urd_for_dependency` if the user has no active URD there.

### Security classification (E08, RF-SEG-08, Law 1712/2014)
- `GET /api/v1/auth/security-levels` — catalog of levels (1 Public, 2 Reserved, 3 Classified).
- `PUT /api/v1/auth/groups/{group_id}/clearance` `{ "max_level": 2 }` — sets the maximum accessible level for a group (requires `USUA_PERM_ADMIN`).
- `GET /api/v1/auth/clearance` — caller clearance: `{user_id, max_level, accessible_levels[]}` (MAX across their groups; ROOT = 3).
- `GET /api/v1/auth/clearance/check?level=N` — `{level, max_level, allowed}` (least privilege: `allowed = max_level ≥ level`).

### `GET /api/v1/audit`
Queries the tenant's immutable audit log (ADR-008), most recent first. Requires `USUA_PERM_ADMIN`. Filters: `?action=&object_type=&object_ref=&actor=&page=1&size=20`. Header `X-Total-Count`.

**Clearance-trimmed (no read-up, RF-SEG-08).** `USUA_PERM_ADMIN` is administrative competence, not a security clearance: administering the tenant does not authorise knowing the classified matters processed within it. Entries whose object is a document or file above the caller's effective clearance are **neither listed nor counted** (`X-Total-Count` reflects the same trimming; otherwise the total would be an oracle for how many classified documents exist). Details:

- **Allow-list, not deny-list**: what gets listed is the types that *cannot* refer to a document or file (identity, RBAC, org structure, instruments and catalogues: `usuario`, `grupo`, `membresia`, `dependencia`, `trd_serie`, `parametro`, `auth_session`…) plus classifiable ones resolving to a reachable level. **Everything else is left out.** Why: `audit_log` is written by eleven services, and several types that do not denote a document carry its identity in the `payload` — `evento` (dead-letter) serialises the whole envelope including `business.tracking_number`, `import` stores the literal list of imported tracking numbers, `file` stores the attachment filename. With a deny-list, every new type or service is born visible and the omission is silent.
- The level is resolved **live** against the tenant schema's `radicados` / `expedientes` — never denormalised, so a reclassification takes effect immediately. The schema is anchored to the request's `tenant_slug`, not to the connection's `search_path`.
- **Fail-closed**: an `object_ref` that resolves to no row (deleted object, different reference format) is left out; what cannot be classified is not declared public. Likewise if the tenant lacks the source tables.
- Filtering by the `object_ref` of an out-of-reach document returns zero entries, indistinguishable from it not existing.
- At maximum clearance (3 = Classified, and ROOT) **no trimming is applied**: the audit log is served in full, which is what that clearance authorises.
- **Declared residue**: `id` is a monotonic sequence, so gaps between visible ids reveal *how many* entries were hidden and, via neighbouring `ts`, *when*. Not closed because `id` is what matches `broken_id` from `/audit/verify`; documented instead.

```json
[ { "id": 42, "ts": "2024-01-15T10:30:00Z", "service": "document-service", "actor": "uuid",
    "canal": "api", "action": "document.radicado_created", "object_type": "radicado",
    "object_ref": "2024-ICETEX-E-000001", "payload": { "...": "..." }, "hash": "sha256..." } ]
```

### `GET /api/v1/audit/verify`
Recomputes the tenant's hash chain and reports its integrity (ADR-008). Requires `USUA_PERM_ADMIN`. **Not** clearance-trimmed: the chain only has evidentiary value if recomputed whole, and over a subset it would always read as "broken". The response is content-free (a boolean and, at most, the id of the row where it breaks), so it reveals nothing about the object.

```json
{ "tenant_slug": "icetex", "ok": true, "broken_id": null }
```
- `ok=false` with `broken_id` = id of the first row whose chain does not match.

---

## tenant-service — port 8002

### `POST /api/v1/tenants`
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

### `GET /api/v1/tenants`
Lists institutions. Query: `?status=active&page=1&size=20`. Header `X-Total-Count`.

### `GET /api/v1/tenants/{id}`
Get by UUID.

### `GET /api/v1/tenants/by-slug/{slug}`
Get by slug. Used by other services to validate the JWT's tenant.

### `PATCH /api/v1/tenants/{id}`
Update `name` or `status`. `slug` and `code` are immutable.

### Tenant administration (E14)
- **Dependencias** (organizational chart): `GET/POST/PATCH /api/v1/dependencias`, `GET /api/v1/dependencias/by-codigo/{codigo}` (hierarchical).
- **Catálogos** (lookups): `GET/POST/PATCH/DELETE /api/v1/catalogos/{catalogo}` — `tipos-identificacion`, `tipos-remitente`, `medios-recepcion`, `tipos-anexo`, `causales`, `formas-envio`, `soportes`, `mensajes-rapidos`.
- **Parameters**: `GET /api/v1/config/params`, `GET /api/v1/config/params/{key}`.
- **Non-business days / holidays**: `GET /api/v1/config/holidays` (`?year=`), `POST /api/v1/config/holidays` (create), `DELETE /api/v1/config/holidays/{id}`, `POST /api/v1/config/holidays/seed` (`{year}` → seeds the automatically computed Colombian holidays).
- **Business days**: `GET /api/v1/business-days/calculate?from_date=&days=` — computes the due date applying weekends + holidays + the tenant's non-working days (basis for RF-RAD-04).

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

### `POST /api/v1/metadata/templates`
Defines the metadata template (JSON Schema) of a document type. A single active template per `tipo_documental`.

```json
// Request
{ "tipo_documental": "Factura", "version": 1, "activo": true,
  "json_schema": { "type": "object", "required": ["valor"],
                   "properties": { "valor": { "type": "number" } } } }
```
- `422 invalid_json_schema` if `json_schema` is not a valid JSON Schema; `409 conflict` if that version, or an active one for the type, already exists.

### `GET /api/v1/metadata/templates`
Lists templates; optional filter `?tipo_documental=Factura`.

### `/api/v1/metadata/elements` (metadata element catalog, RF-MET-03)
CRUD for reusable metadata field definitions (`POST`/`GET`/`GET {id}`/`PATCH {id}`/`DELETE {id}`). Attributes: `clave`, `etiqueta`, `tipo_dato` (text/number/date/boolean/select), `longitud`, `ocurrencia_min/max`, `modificable`, `valor_default`, `opciones`, `orden`, `searchable`, `mapeo_dublin_core` (RF-MET-09 hook). `409` if the key already exists.

### `GET /api/v1/documents`
List radicados. Filters: `?doc_type=E&status=registered&page=1&size=20`. **Filter by metadata** (RF-MET-02): `?meta.<campo>=valor` (one or several) uses JSONB containment over the GIN index, e.g. `?meta.area=juridica`.

### `GET` / `PATCH /api/v1/documents/{id}/disposition`
Document disposition metadata (RF-MET-08): `programa`, `retention_until`, `accion` (conserve/delete/transfer/select), `confirmado`, `marcado_eliminacion`. `PATCH` merges the provided fields. `404` if the radicado does not exist.

### `GET /api/v1/documents/{id}`
Get by UUID. Includes the list of anexos (attachments).

### `GET /api/v1/documents/by-tracking/{tracking_number}`
Get by radicado number (e.g.: `2024-ICETEX-E-000001`).

### `PATCH /api/v1/documents/{id}`
Update `subject`, `dest_dept`, `observations`, `status`, `pages`.
`tracking_number`, `doc_type`, `year`, `sequence` are immutable.

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
{ "url": "http://minio:9000/orpycamcp-icetex-documents/uuid/solicitud.pdf?X-Amz-...", "expires_in": 3600 }
```

### `GET /api/v1/storage/files/{file_id}/verify`
Re-verifies integrity (RF-DIG-02): downloads the object from MinIO, recomputes the SHA-256 and compares it with the recorded one.

```json
{ "file_id": "uuid", "expected_sha256": "...", "actual_sha256": "...", "integrity_ok": true }
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
- `POST /api/v1/preservacion/eventos` `{object_ref, tipo, resultado, hash_before?, hash_after?, detalle?}` · `GET /api/v1/preservacion/eventos?object_ref=` — immutable PREMIS log (INGESTA/FIJACION/MIGRACION/VALIDACION_PDFA/WORM/REPLICA).
- `POST /api/v1/preservacion/aip` `{expediente_id, documentos:[{document_id, nombre, valor_huella?}], indice_sha256?}` — packages the **AIP** (BagIt + PREMIS) and returns `bagit`, `premis_xml`, `aip_sha256`. `GET /api/v1/preservacion/aip/{expediente_id}` retrieves the current AIP.

---

## workflow-service — port 8006

> Requires the `X-Tenant-Slug` and `X-User-Id` headers.

> **Automatic distribution (RF-FLU-04):** in addition to the endpoints, workflow-service consumes the `orpycamcp.document.events` stream. When a document is registered (`document.radicado.created`) it evaluates the active rules and, if any matches, **auto-assigns** the radicado to its dependencia (step + append-only event, author = system). It is idempotent (it does not redistribute if the radicado already has steps).

### `POST /api/v1/workflows/assign`
Assigns a radicado to a dependencia (first step or reassignment).

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
Transfers the radicado to another dependencia. Requires `from_dept`.

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

### `POST /api/v1/workflows/overdue/scan`
Overdue sweep (RF-FLU-07): detects active overdue steps not yet alerted, emits `workflow.step.overdue` to the bus (→ notifications) and marks them to avoid duplication. Idempotent per step; intended for periodic invocation by a scheduler. Responds `{ "alerted": N }`.

### Sequential approvals (vistos buenos) (legacy parity)
- `POST /api/v1/workflows/{radicado_id}/vistos-buenos` `{revisores: [uuid,...]}` — creates the ordered review chain.
- `POST /api/v1/workflows/{radicado_id}/vistos-buenos/decidir` `{aprobar, comentario?}` — the reviewer whose turn it is (lowest pending order) approves/rejects; `403` if it is not their turn; a rejection stops the chain.
- `GET /api/v1/workflows/{radicado_id}/vistos-buenos` — chain + global status (`en_revision|aprobado|rechazado`).

### `POST /api/v1/workflows/{radicado_id}/devolver` (return to sender)
Re-routes a misassigned radicado: `{ "to_dept": "...", "causal": "DEV-DEP", "comentario"? }`. Closes the active step as `returned` and creates a new one towards `to_dept`, with the reason code (tenant `causales` catalog) in the history. `404` if there is no active step.

### Procedure transactions (RF-FLU-01)
- `GET /api/v1/workflows/transaction-types` — catalog (informar, NRR, agendar, no_agendar, change_folder, marcar_leido, validate_trd_send, solicitar_firma, vobo, cerrar_exp, anular…), with their atomic permission and state effect.
- `POST /api/v1/workflows/{radicado_id}/transactions` `{ "tipo_tx": "anular", "comentario": "...", "detalles": {} }` — executes the transaction: applies the effect to the active step (e.g. `marcar_leido`→in_progress, `anular`→cancelled) and records the append-only event. `404 unknown_transaction` / `no_flow`.

### `POST /api/v1/workflows/{radicado_id}/rollback`
Reverts the last assignment (RF-FLU-08): cancels the current step —only if it is still `pending`— and reactivates the previous one, atomically, with a `tipo_tx=rollback` event. `409 no_active_step` (no active step) or `cannot_rollback_initial` (it is the initial registration).

### `POST /api/v1/workflows/reassign/cascade`
Reassigns, in a single transaction, all the active steps of a user or dependencia to a new responsible party, leaving no orphan radicados (RF-FLU-08).

```json
// Request (provide from_assigned_to or from_dept)
{ "from_dept": "Dirección Jurídica", "to_assigned_to": "uuid" }
// Response 200
{ "reassigned": 12 }
```

### `GET /api/v1/workflows/{radicado_id}/events`
**Append-only** history of the radicado's transactions (RF-FLU-02, unalterable). Unlike `/history` (a projection of steps), it records every transaction and its table rejects UPDATE/DELETE. **Access control (E05 §10 / RF-SEG-08):** this is the *operational route map* (Channel B) — visible to any user who can **read** the radicado, scoped by their clearance (no read-up); requires `X-User-Id`, and a radicado above the clearance returns an empty history. No admin permission required (that is the `audit_log` forensic audit).

```json
[ { "id": "uuid", "tipo_tx": "assign", "from_dept": null, "to_dept": "Ventanilla",
    "actor": "uuid", "comentario": "...", "detalles": { "step_number": 1 }, "ts": "2024-01-15T10:30:00Z" } ]
```

### `GET /api/v1/workflows/{radicado_id}/current`
Current active step of the radicado. `404` if it is closed or unassigned.

### `GET /api/v1/workflows/pending`
Pending radicados. Filters: `?dept=Dirección+Jurídica&assigned_to={uuid}&page=1&size=20`.

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
- `POST /api/v1/transferencias` `{tipo: primaria|secundaria, expediente_id, ubicacion_origen_id?, ubicacion_destino_id?}` — creates (precondition: expediente `closed`; `409 expediente_not_closed` otherwise). Cycle `preparada→enviada→recibida|rechazada`.
- `POST /{id}/enviar`, `POST /{id}/recibir` (on receipt, the expediente moves to `transferred` and is frozen), `POST /{id}/rechazar`.
- `GET /api/v1/transferencias?estado=&tipo=`, `GET /{id}`, `GET /{id}/fuid` (deliverable: FUID (single documentary inventory format) from E17 filtered by the expediente).

#### Physical archive (E17, ADR-017)
- `POST/GET /api/v1/ubicaciones`, `GET /api/v1/ubicaciones/{id}` — recursive topology (site→…→drawer); `codigo` unique per tenant; derived `ruta`. Filters `?parent_id=&activo=`.
- `POST/GET /api/v1/unidades`, `GET /api/v1/unidades/{id}` — conservation units (box|folder|…); when `ubicacion_id` is provided, the **topographic signature** is derived (`DEP01-E05-C0124`, unique). Filters `?ubicacion_id=&tipo=`.
- `POST /api/v1/unidades/{id}/expedientes` — link an expediente to a unit (`folio_inicio/fin`).
- `GET /api/v1/unidades/{id}/expedientes` — what the unit contains. `GET /api/v1/expedientes/{id}/unidades` — where the expediente is (signature + range).
- **Loans (RF-ARF-07)**: `POST /api/v1/unidades/{id}/prestamos` (409 if already lent out), `POST /api/v1/prestamos/{id}/devolver`, `GET /api/v1/prestamos?estado=prestado|devuelto|vencido`, `GET /api/v1/unidades/{id}/prestamos`. `vencido` is derived from `fecha_devolucion_esperada`.
- **History (RF-ARF-08)**: `GET /api/v1/unidades/{id}/movimientos` — append-only movements (RETIRADO/DEVUELTO/…).
- **FUID (RF-ARF-10)**: `GET /api/v1/fuid` — documentary inventory (expediente↔unit↔signature↔series) in JSON with consecutive ordering and AGN 042/2002 fields; filters `?ubicacion_id=&trd_serie_id=`. `GET /api/v1/fuid.xml` exports as XML.

#### TRD/CCD (E04, ADR-015)
- `POST/GET/PATCH /api/v1/trd` — documentary series and **subseries**. Fields: `code`, `name`, `parent_id` (subseries), `archivo_gestion_years`/`archivo_central_years` (two-phase retention), `disposition` (AGN: `CT`/`E`/`S`/`M`), `version`, `valid_from`.
- `GET /api/v1/trd/{serie_id}/retention?closed_at=YYYY-MM-DD` — **retention calendar**: `fin_archivo_gestion`, `fin_archivo_central` and final disposition (`disposition_code`/`disposition_label`) derived from the series and the closing date.

#### `POST /api/v1/expedientes/{id}/radicados/batch` (bulk inclusion)
Links several radicados at once `{radicados:[{radicado_id, tracking_number, content_hash?}]}` (up to 500; idempotent, skips those already linked); regenerates the index only once. Returns `{vinculados, omitidos, total}`. `409` if the expediente is not `open`.

#### Expediente lifecycle (E02)
- `POST /api/v1/expedientes/{id}/close` — closes the expediente (`open→closed`), materializes the TRD disposition in `disposition` and **generates+signs the final version of the electronic index** via signature-service (best-effort; leaves `expediente_indice.estado='firmado'`).
- `POST /api/v1/expedientes/{id}/transfer` — transfers (`closed→transferred`). `409` if the transition is invalid.
- `GET /api/v1/expedientes/{id}/eventos` — **append-only route map of the expediente** (E02 §9, RF-EXP-09): chronological list of `{id, accion, actor, comentario, detalles, ts}` with `accion` ∈ `abierto|radicado_vinculado|radicado_excluido|cerrado|transferido|...`. **Access control (Channel B / RF-SEG-08):** visible to anyone who can **read** the expediente, scoped by their clearance (no read-up); no admin required. Distinct from `audit_log` (forensic audit) and `/unidades/{id}/movimientos` (physical movements, E17).
- `GET /api/v1/expedientes/{id}/foliado` — foliation: radicados ordered with a sequential `folio` (`{expediente_id, code, total_folios, items[]}`). (The normative **electronic index** is E15, below.)

#### Electronic index (E15, AGN Agreement 001/2024)
- `GET /api/v1/expedientes/{id}/indice` (`?version=N`) — current index or by version: `{version, estado, num_documentos, xml_sha256, algoritmo, items[]}`.
- `GET /api/v1/expedientes/{id}/indice.xml` (`?version=N`) — XML body (`application/xml`, namespace `urn:orpycamcp:indice:v1`).
- `GET /api/v1/expedientes/{id}/indice/versions` — version history (append-only) with `X-Total-Count`.
- `GET /api/v1/expedientes/{id}/indice/verify` (`?version=N`) — verifies integrity: compares the index's `valor_huella` against the current hash of each document (`{valido, documentos[], firma{presente,valida}, fecha_verificacion}`).
- `POST /api/v1/expedientes/{id}/indice/rebuild` — regenerates (new version if the set changed; `{unchanged:true}` if identical). **Version 0** is created automatically when the expediente is created.
- When linking a radicado (`POST /api/v1/expedientes/{id}/radicados`) a `content_hash` (SHA-256 of the document) can be supplied, which the index uses as `valor_huella`. Linking/excluding regenerates the index automatically; on a non-`open` expediente it returns `409`.

#### `GET /api/v1/expedientes/search` (FTS search, E09)
Full-text search of expedientes (ADR-014). `q` optional (`tsvector` over `code`/`name`/`description` + `ts_rank` ranking); filters `?status=&date_from=&date_to=&meta.<campo>=`. Header `X-Total-Count`. Without text = filtered query ordered by `opened_at`.

#### `/api/v1/tipos-documentales` (3rd TRD level, RF-MET-07)
CRUD for the catalog of documentary types (`POST`/`GET`/`GET {id}`/`PATCH {id}`/`DELETE {id}`). Fields: `code`, `nombre`, `trd_serie_id` (series/subseries), `obligatorio`, `metadata_template` (associated template). Filters `?trd_serie_id=&activo=`. `409` if the `(serie, code)` already exists.

#### `GET /api/v1/expedientes`
List with filters `?status=open&page=1&size=20`.

#### `GET /api/v1/expedientes/{id}`
Get by UUID. Includes the list of linked radicados.

#### `PATCH /api/v1/expedientes/{id}`
Update `name`, `description`, `trd_serie_id`, `status`.
Valid transitions: `open → closed → transferred`. It cannot be reopened.

#### `POST /api/v1/expedientes/{id}/radicados`
Link a radicado to the expediente.
```json
{ "radicado_id": "uuid", "tracking_number": "2024-ICETEX-E-000001" }
```

#### `DELETE /api/v1/expedientes/{id}/radicados/{radicado_id}`
Unlink a radicado. `204`.

### TRD

#### `POST /api/v1/trd`
Create a documentary series.
```json
{ "code": "100", "name": "Contratos", "retention_years": 5, "total_retention": 10, "disposition": "conserve" }
```

#### `GET /api/v1/trd`
List series. Filter: `?is_active=true&page=1&size=50`.

#### `GET /api/v1/trd/{id}`
Get series by UUID.

#### `PATCH /api/v1/trd/{id}`
Update series (except `code`).

---

## notification-service — port 8007

### `POST /api/v1/notifications/send`
Manual sending of an email notification. Responds `202` (accepted for async sending). The history is persisted in the database of the indicated `tenant_slug`.

```json
{ "recipient_email": "juridica@icetex.gov.co", "subject": "Nuevo radicado", "body": "...", "tenant_slug": "icetex" }
```

### `GET /api/v1/notifications/history`
Notification history of the tenant (persisted in the database per institution). Requires `X-Tenant-Slug`. Query: `?limit=20`.

### `GET /api/v1/notifications/{id}`
Get notification by UUID. Requires `X-Tenant-Slug`. `404` if it does not exist.

### Electronic signature — signature-service, port 8008 (E06, ADR-016)
Pluggable provider (`nativa` by default: hash+identity; PKI XAdES/PAdES/TSA as a swap).
- `POST /api/v1/signature/sign-xml` `{payload_xml, objeto_tipo, objeto_id?, formato?, firmante_id?}` — signs the payload and persists the signature; returns `{firma_id, payload_sha256, provider, ...}`.
- `POST /api/v1/signature/verify` `{firma_id, payload_xml}` — recomputes and returns `{valida}` (detects tampering). `404` if the signature does not exist.

### MCP layer (E18, ADR-019) — mcp-server, port 8009
Separate service (not behind the gateway): exposes the API as *tools* to agents/LLMs, while itself being a client of the gateway.
- `GET /api/v1/mcp/tools` — catalog of tools visible to the user (filters by `X-User-Permissions`; `X-User-Roles` with `ROOT` sees all).
- `POST /api/v1/mcp/tools/{name}/invoke` `{...args}` — invokes the tool: propagates `Authorization` + `X-Tenant-Slug` to the gateway. Write operations require `X-Confirm-Write: true` (`412` if missing) and the corresponding permission (`403` if missing). Tools: `radicar_documento`, `consultar_radicado`, `buscar_radicados`, `consultar_expediente`, `indice_expediente`, `listar_trd`, `bandeja_tramite`, `consultar_fuid`.

### Knowledge layer (E21, ADR-006) — knowledge-service, port 8011
**Advisory/derived and opt-in** layer (not a source of truth). Embeddings with a pluggable provider (local stub by default).
- `POST /api/v1/knowledge/ingest` `{source_type, source_ref, texto, metadata?, acl?}` — computes the embedding and persists the fragment (pgvector) with its ACL.
- `POST /api/v1/knowledge/search` `{query, top_k?, acl?}` — semantic retrieval (cosine kNN) with **ACL pre-filtering** (only fragments whose `acl` contains the filter). Returns `[{id, source_type, source_ref, texto, metadata, score}]`.

### Outbound webhooks (E11, ADR-018)
- `POST /api/v1/webhooks` `{url, event_types: [...], secret?}` — subscribes an external endpoint (empty `event_types` = all events). The `secret` is not returned.
- `GET /api/v1/webhooks` — lists the subscriptions (`X-Total-Count`).
- `DELETE /api/v1/webhooks/{id}` — deletes a subscription.
- When a domain event occurs, OrpycaMCP `POST`s the canonical envelope to the matching subscriptions, signed in the `X-OrpycaMCP-Signature: sha256=<hmac>` header (best-effort with retries). The receiver must be idempotent.

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
  "job_id": "uuid", "job_type": "documents",
  "status": "completed",  // pending | processing | completed | failed
  "total_items": 2, "processed_items": 2, "failed_items": 0,
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

### Electronic signature (E06, ADR-016)
- `POST /api/v1/documents/{id}/signatures` `{ "content_hash": "<sha256 hex>", "anexo_id"?, "reason"? }` — signs the radicado/anexo: records identity (gateway claims), `content_hash` and timestamp. `422` if the hash is not SHA-256.
- `GET /api/v1/documents/{id}/signatures` — lists the signatures (`X-Total-Count`).
- `GET /api/v1/documents/{id}/signatures/verify?content_hash=<hex>` — `{content_hash, valid, signatures[]}`: `valid=true` if any signature of the radicado matches that hash (if the content changed, it stops verifying).

### Postal dispatches (E20)
- `POST /api/v1/documents/{id}/envios` `{operador, destinatario?, direccion?}` — dispatches the radicado via a postal operator (4-72/servientrega/otro); generates the tracking guide and `registrado` status.
- `GET /api/v1/documents/{id}/envios` · `GET /api/v1/envios/{id}` — dispatches of the radicado / detail with event history.
- `POST /api/v1/envios/{id}/estado` `{estado, descripcion?}` — updates the delivery status (operator callback/polling). State machine `registrado→en_transito→entregado|devuelto|fallido`; `409` if the transition is invalid.

### `POST /api/v1/ingest/email/poll` (email ingestion, E19)
Internal operation (requires JWT). Fetches the unread emails from the configured IMAP mailbox, creates one Entrada (inbound) radicado per email (`metadata.source=email`, sender and attachments in `metadata`) and marks them as read. Tenant in `X-Tenant-Slug`. Response `{ "ingested": n, "tracking_numbers": [...] }`. `503` if IMAP is not configured (`IMAP_HOST` empty).

### `POST /api/v1/public/{tenant}/pqrs` (citizen registration, Law 1755/2015)
**No authentication.** A citizen files a PQRS (citizen request: petition/complaint/claim): `{ "tipo": "peticion|queja|reclamo|sugerencia|denuncia", "nombre", "identificacion"?, "email"?, "asunto", "descripcion" }`. Creates an Entrada (inbound) radicado (`metadata.canal=pqrs_ciudadano`) and returns `{radicado_id, tracking_number, verification_code}` for later tracking with the verification endpoint.

### `GET /api/v1/public/{tenant}/verify/{code}` (public lookup, E13)
**No authentication.** Verifies the traceability of a radicado by its verification code (an unguessable token, distinct from the radicado number). The tenant travels in the path. Returns only NON-sensitive information: `tracking_number`, `doc_type`, `status`, `registered_at`, `dest_dept`. `404` if the code does not exist; `400` if the tenant slug is invalid.

### `GET /api/v1/reports/radicados`
Statistical summary of the tenant's radicados (E09): `total` and counts `by_doc_type`, `by_status`, `by_month` (`YYYY-MM`), `by_dependencia`. Optional filters `?date_from=&date_to=`. Native SQL aggregations (`GROUP BY`/`date_trunc`).

### `GET /api/v1/reports/radicados.csv`
Exports the summary in **CSV** (`seccion,clave,valor` with type/status/month/dependencia sections) — for periodic reports to the AGN/oversight bodies. Same filters `?date_from=&date_to=`.

```json
{ "total": 8, "by_doc_type": {"E": 5, "S": 3}, "by_status": {"registered": 6, "archived": 2},
  "by_month": [{"month": "2024-01", "count": 4}, {"month": "2024-02", "count": 4}] }
```

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
