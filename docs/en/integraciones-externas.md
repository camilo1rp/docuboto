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

## 2. WORM / immutability (MinIO Object Lock) — `storage-service`

| | |
|---|---|
| Current state | Preservation plan + PREMIS events + AIP/BagIt. The `WORM` event is recorded, but there is **no** real Object Lock. |
| Swap point | `storage-service` (MinIO client + preservation bucket; when persisting the AIP). |
| Regulation | Acuerdo AGN 001/2024 (Colombian National Archive agreement) Art. 4.3.2.6; Decreto 2609/2012 Art. 26. |

**To enable WORM:**
1. Create a **preservation bucket** with *versioning* and **Object Lock** enabled (*compliance* mode).
2. When packaging the AIP — Archival Information Package — (`POST /api/v1/preservacion/aip`), upload the object with a `retention until` derived from the **TRD** (E04, `archivo_central_years`) and, if applicable, a *legal hold*.
3. Record the `WORM` event (already supported) with the applied retention date.
4. Requires MinIO/S3 with Object Lock; the retention calculation already exists (E04 `GET /trd/{id}/retention`).

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
GATEWAY_URL=http://localhost:8080 \
MCP_TENANT_SLUG=icetex \
MCP_USER_PERMISSIONS=RADI_CREAR,... \
# (a) static token (no refresh):   MCP_BEARER_TOKEN=<jwt>
# (b) per-session refresh:         MCP_REFRESH_TOKEN=<refresh-token>
# (c) ROPC (username/password):    MCP_AUTH_USERNAME=ana MCP_AUTH_PASSWORD=...
python -m app.mcp_stdio
```

With (b)/(c) the mcp-server **obtains and refreshes** the Keycloak access token via `auth-service` (`/api/v1/auth/token` and `/api/v1/auth/refresh`, public in the gateway) and caches it until expiry.

**HTTP streamable transport (remote clients):** the client connects to the **`POST http://mcp-server:8009/mcp`** endpoint; the session context arrives via per-request **headers**: `Authorization: Bearer <jwt>`, `X-Tenant-Slug`, `X-User-Permissions`, `X-User-Roles`.

In both cases the server propagates `Authorization` + `X-Tenant-Slug` to the gateway (which resolves authentication/RBAC/tenant) and filters the visible tools by the user's permissions (ROOT sees all). Write operations remain subject to the MCP client's tool approval (human-in-the-loop) plus the permission scope.

**Resources** (`orfeo://normativa`, templates `orfeo://radicado/{id}`, `orfeo://expediente/{id}`, `orfeo://expediente/{id}/indice`) and **prompts** (`radicar_pqrsd`, `resumen_expediente`, `buscar_antecedentes`) are available on both transports.

The MCP layer is thus **complete** (tools + resources + prompts + both transports + per-session OAuth).

---

## 5. Knowledge layer — real embeddings/LLM — `knowledge-service`

| | |
|---|---|
| Current state | Deterministic **stub** embedding (hash→vector dim 64). pgvector + retrieval with ACL pre-filtering already work. |
| Swap point | `services/knowledge-service/app/services/embeddings.py` (`embed`). |
| Selector | `EMBEDDING_PROVIDER` / `EMBEDDING_DIM` (config). |
| Decision | ADR-006 (configurable AI provider, local by default). |

**To enable real embeddings/RAG:**
1. Connect an embeddings model (e.g. local `sentence-transformers` or an API) by implementing `embed(text) -> list[float]`; adjust `EMBEDDING_DIM` and the `vector(N)` column (migration) to the model's dimension.
2. For grounded RAG with citations and TRD/dependencia suggestions (advisory), connect a **pluggable** LLM (ADR-006); keep the **ACL pre-filtering** (`acl @>`) before retrieval.
3. It is an **advisory/derived and per-tenant opt-in** layer: it is not a source of truth.

---

## 6. Real postal operator (4-72 / Servientrega) — `document-service`

| | |
|---|---|
| Current state | **Stub** provider (deterministic guía, no real API). |
| Swap point | `services/document-service/app/services/postal_provider.py` (`generar_guia`). |

**To enable the real operator:** implement `generar_guia(operador, radicado_id)` (and, if applicable, the status query) against the operator's API with its credentials; the delivery status is already updated via `POST /api/v1/envios/{id}/estado` (callback/polling).

---

## Summary

| Capability | Service | Swap | Needs |
|---|---|---|---|
| PKI digital signature | signature-service | `signer.py` | X.509 cert + TSA + lib |
| WORM (Object Lock) | storage-service | MinIO client | MinIO with Object Lock |
| PDF/A | storage-service | normalization | veraPDF |
| MCP protocol (tools+resources+prompts, stdio+HTTP, OAuth) ✅ | mcp-server | done (`mcp_app.py`+`mcp_resources.py`+`oauth.py`+`/mcp`) | — |
| Embeddings/RAG | knowledge-service | `embeddings.py` | model/LLM |
| Postal operator | document-service | `postal_provider.py` | API credentials |

All integrations preserve their contract; enabling each one means implementing the adapter and providing the dependency/credential — **with no changes to the domain logic**.
