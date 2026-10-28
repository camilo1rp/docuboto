# External integrations (operations notes)

Several OrpycaMCP capabilities are implemented with a **pluggable provider** and a **functional default stub**, so that the system operates end-to-end without heavy external dependencies. This guide indicates, for each one, **where to plug in the real component** when the environment is available (certificates, WORM storage, libraries, models), which **contract stays stable**, and the activation **steps**.

Common principle: the contract (function signatures / REST endpoints) **does not change** when swapping the stub for the real provider. Only the adapter is implemented and the configuration is adjusted.

---

## 1. Qualified digital signature (PKI / XAdES) — `signature-service`

| | |
|---|---|
| Current state | Native **electronic** signature (SHA-256 + identity + seal). Detects alteration; it is **not** a qualified digital signature. |
| Swap point | `services/signature-service/app/services/signer.py` (`firmar` / `verificar` functions). |
| Selector | `SIGNER_PROVIDER` (config; `nativa` by default). |
| Regulation | Decreto 2364/2012 (Colombian electronic signature law) → certified for procedures that require a **digital** signature. |

**To enable PKI:**
1. Add a signing library (e.g. `pyhanko`/`endesive` + `cryptography`) to `services/signature-service/requirements.txt`.
2. Implement an `xades` provider in `signer.py` with the same interface: `firmar(payload, firmante) -> {payload_sha256, firma_blob, provider}` and `verificar(...)`, using an **X.509** certificate, a **TSA (RFC 3161)** timestamp and, if applicable, **CRL/OCSP** validation.
3. Provision the certificate and the TSA endpoint (environment variables/secrets), select it with `SIGNER_PROVIDER=xades`.
4. The index signing at closure (`archive → signature-service /sign-xml`) and `/verify` already consume this contract: they switch to a digital signature with no changes in archive.

---

## 2. WORM / immutability (S3 Object Lock) — `storage-service`

| | |
|---|---|
| Current state | Preservation plan + PREMIS events + AIP/BagIt. The `WORM` event is recorded, but there is **no** real Object Lock. |
| Swap point | `storage-service` (S3 client + preservation bucket; when persisting the AIP). |
| Regulation | Acuerdo AGN 001/2024 (Colombian National Archive agreement) Art. 4.3.2.6; Decreto 2609/2012 Art. 26. |

**To enable WORM:**
1. Create a **preservation bucket** with *versioning* and **Object Lock** enabled (*compliance* mode).
2. When packaging the AIP — Archival Information Package — (`POST /api/v1/preservacion/aip`), upload the object with a `retention until` derived from the **TRD** (E04, `archivo_central_years`) and, if applicable, a *legal hold*.
3. Record the `WORM` event (already supported) with the applied retention date.
4. Requires an S3 store with **verified** Object Lock (conformance battery, ADR-027); the retention calculation already exists (E04 `GET /trd/{id}/retention`).

---

## 3. PDF/A validation (veraPDF) — `storage-service`

| | |
|---|---|
| Current state | E07 delivers *best-effort* PDF/A without validation. Preservation does not validate conformance. |
| Swap point | `storage-service` (preservation ingestion/normalization step). |
| Regulation | Decreto 2609/2012 Art. 32 (open standards); ISO 19005 (PDF/A). |

**To enable veraPDF:**
1. Have **veraPDF** (Java CLI) or an accessible validator microservice available.
2. During normalization to PDF/A-1b/2b, invoke veraPDF and record the result as a `VALIDACION_PDFA` event (`ok`/`fallo`) in preservation (already supported).
3. Reject/flag non-conforming objects before packaging the AIP.

---

## 4. MCP layer — MCP server — `mcp-server`

| | |
|---|---|
| Current state | **Complete MCP server** (official `mcp` SDK) over **stdio** and **HTTP streamable**: exposes **tools** (catalog), **resources** (`orfeo://…`) and **prompts**, with **per-session OAuth** (obtains/refreshes the Keycloak token via auth-service). Open refinement: proactive refresh scheduling. |
| Implementation | `services/mcp-server/app/mcp_app.py` (tools) + `app/mcp_resources.py` (resources+prompts) + `app/mcp_stdio.py` (stdio entrypoint) + the **`/mcp`** endpoint on the FastAPI app (`app/main.py`, `StreamableHTTPSessionManager`). The `catalog.py` + `dispatcher.py` core is reused unchanged. |
| Regulation/decision | Constitution §1; ADR-019. |

**stdio transport (local desktop-style client):** the client launches the process and talks over stdio; the session is configured via environment. There are three **token** modes (precedence top to bottom):

```bash
GATEWAY_URL=http://localhost:19080 \
MCP_TENANT_SLUG=icetex \
# (a) static token (no refresh):   MCP_BEARER_TOKEN=<jwt>
# (b) per-session refresh:         MCP_REFRESH_TOKEN=<refresh-token>
# (c) ROPC (username/password):    MCP_AUTH_USERNAME=ana MCP_AUTH_PASSWORD=...
python -m app.mcp_stdio
```

With (b)/(c) the mcp-server **obtains and refreshes** the Keycloak access token via `auth-service` (`/api/v1/auth/token` and `/api/v1/auth/refresh`, public in the gateway) and caches it until expiry.

**HTTP streamable transport (remote clients):** **`POST https://<host>/api/v1/mcp/stream`** through the api-gateway, which validates the JWT and injects `X-Tenant-Slug`. (`POST http://mcp-server:8009/mcp` still exists for access inside the Docker network.) The session context arrives via two headers and nothing else: `Authorization: Bearer <jwt>` and `X-Tenant-Slug`.

> **`X-User-Permissions` and `X-User-Roles` are no longer read, and `MCP_USER_PERMISSIONS`/`MCP_USER_ROLES` have been removed.** No component produced the former — the gateway drops it as forgeable — so it always arrived empty and every permissioned tool was invisible over this transport. The environment variables were worse: they *could* be set, so whoever configured them believed they were granting something, when all they achieved was seeing tools offered that the backend would deny on use. **Permissions are resolved against `GET /api/v1/auth/me` with the caller's token**, one resolution for all three transports.

The scope framework is offered two equivalent ways: the **`mi_alcance`** tool and the **`orfeo://alcance`** resource — what the person can execute, what they can read, and what they **cannot** with its reason and its **alternative route**.

In both cases the server propagates `Authorization` + `X-Tenant-Slug` to the gateway (which resolves authentication/RBAC/tenant) and filters the visible tools by the user's permissions (ROOT sees all). Write operations remain subject to the MCP client's tool approval (human-in-the-loop) plus the permission scope.

**Resources** (`orfeo://normativa`, templates `orfeo://radicado/{id}`, `orfeo://expediente/{id}`, `orfeo://expediente/{id}/indice`) and **prompts** (`radicar_pqrsd`, `resumen_expediente`, `buscar_antecedentes`) are available on both transports.

The MCP layer is thus **complete** (tools + resources + prompts + both transports + per-session OAuth).

---

## 5. Knowledge layer — FTS gateway / external PPI — `knowledge-service`

| | |
|---|---|
| Current state | knowledge-service **no longer hosts embeddings** ([ADR-046](adr/ADR-046-conocimiento-sin-modelos.md)). FTS gateway to document-service (`/search`, `/antecedentes`); `/rag` returns `503` without an external PPI provider; `/availability` reports the mode. No pgvector, no ingest consumer, no `kb_suggestions`. |
| Intelligence | When a PPI is configured, intelligence comes from that external provider; the local fallback is document-service FTS (Docuboto calls no models). |
| Gate | `asistente_habilitado` + `PERM_CONOCIMIENTO` ([ADR-037](adr/ADR-037-asistente-activable-por-entidad.md)). |
| Decision | [ADR-046](adr/ADR-046-conocimiento-sin-modelos.md) (withdraws the local model layer); [ADR-045](adr/ADR-045-pasarela-de-inteligencia-y-protocolo-ppi.md) (PPI); ADR-006 remains historical for the original pluggable design. |

**What left Docuboto (ADR-046):** there is no `embeddings.py`, `EMBEDDING_PROVIDER`, own pgvector index, or local LLM. Enabling generation/semantic RAG means configuring an external PPI, not plugging a model into knowledge-service.

---

## 6. Real postal operator (4-72 / Servientrega) — `document-service`

| | |
|---|---|
| Current state | **Stub** provider (deterministic guía, no real API). |
| Swap point | `services/document-service/app/services/postal_provider.py` (`generar_guia`). |

**To enable the real operator:** implement `generar_guia(operador, radicado_id)` (and, if applicable, the status query) against the operator's API with its credentials; the delivery status is already updated via `POST /api/v1/envios/{id}/estado` (callback/polling).

---

## 7. Migration from legacy Orfeo — **out of scope for this repository**

**Scope decision: this codebase does not carry, and will not carry, data-migration
tooling for existing Orfeo installations.** That work is done externally, outside
the repository.

### Why

Every institution configured its Orfeo differently: 14- or 15-digit registration
numbers, metadata in dynamic tables, its own password scheme, different database
engines. A generic extractor does not exist; what exists is per-installation
mapping work. Putting it here would mean maintaining a set of special cases in
the core that help nobody else and age with every client.

### What this repository does provide, and it is enough

The way in is the **interoperable importer**, which is generic and documented:

- `POST /api/v1/import` in `document-service` — takes a ZIP package with
  `manifiesto.json`, `esquema/radicado.schema.json` (JSON Schema draft 2020-12
  **published inside the package itself**), the records as JSON and their
  attachments.
- **All-or-nothing** validation: if a schema or a SHA-256 does not match, no
  partial state is left in the database.
- **Identity preserved**: the original registration number is not regenerated
  (AGN Agreement 060/2001, Law 594/2000 art. 19). A collision is skipped, never
  overwritten.
- Idempotent by number, which allows repeated incremental loads.

In short: the input contract is stable and public. **What gets built outside is
the translator** from the source system into that format.

### Practical consequences

- Orfeo-specific ETL, per-client adapters and legacy-database extraction scripts
  are not accepted in this repository.
- The analysis material from real installations lives in the private
  `documentos/planesMigracion/` repository and is **business-rule reference**,
  not migration code.
- If a migration hits a limitation of the **generic importer** — say the accepted
  registration-number format, or reconciling the numbering sequence after a load
  — that is work for this repository, because it affects the input contract and
  benefits any source, not only Orfeo.

## Summary

| Capability | Service | Swap | Needs |
|---|---|---|---|
| PKI digital signature | signature-service | `signer.py` | X.509 cert + TSA + lib |
| WORM (Object Lock) | storage-service | S3 client | RustFS with Object Lock (ADR-027) |
| PDF/A | storage-service | normalization | veraPDF |
| MCP protocol (tools+resources+prompts, stdio+HTTP, OAuth) ✅ | mcp-server | done (`mcp_app.py`+`mcp_resources.py`+`oauth.py`+`/mcp`) | — |
| Knowledge FTS / PPI | knowledge-service | gateway to document-service (+ external PPI when configured) | external PPI (optional); no local models |
| Postal operator | document-service | `postal_provider.py` | API credentials |
| Migration from Orfeo | — | **out of scope** | external translator into `POST /api/v1/import` |

All integrations preserve their contract; enabling each one means implementing the adapter and providing the dependency/credential — **with no changes to the domain logic**.

Migration is the exception, and that is why it has no service: it is not an adapter that plugs in here, but work done outside against a stable input contract (§7).
