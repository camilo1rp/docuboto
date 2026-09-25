# OrpycaMCP

> **Free, open-source Document Management System (DMS / SGDEA) for Latin American institutions**
> FastAPI microservices · PostgreSQL · RustFS (S3) · Keycloak · Redis
> Published under **AGPL v3**

[![CI/CD](https://img.shields.io/badge/CI%2FCD-GitLab%20CI-blue)](https://gitlab.com/orpyca/orfeo_mcp)
[![License](https://img.shields.io/badge/License-AGPL%20v3-green)](https://www.gnu.org/licenses/agpl-3.0)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue)](https://www.python.org/)
[![Docker](https://img.shields.io/badge/Docker-24.0%2B-blue)](https://www.docker.com/)

---

## What is OrpycaMCP?

A **Document Management System (DMS / SGDEA — Sistema de Gestión Documental Electrónico de Archivo)** that governs the **complete lifecycle of official documents**: registration (radicación), distribution (flujos), filing (expedientes), retention scheduling (TRD), electronic signature, preservation, and final disposition — all with legal traceability.

OrpycaMCP is a modern reimplementation of the [Orfeo GPL](https://github.com/orfeosgd/orfeo) system. The business logic (radicados, expedientes, TRD, flujos) is faithfully preserved from the original; the stack changes from a PHP monolith → **FastAPI microservices** (async, `asyncpg` with raw SQL — no ORM), plus an **MCP layer** that exposes the platform as tools to AI agents (the *MCP* in OrpycaMCP).

### Who needs this?

Public and private institutions in Latin America required by law (e.g., Colombian **Ley 594/2000**, **Acuerdo AGN 060/2001**, **Acuerdos AGN 001/2024 · 042/2002 · 003/2015**, **Ley 1712/2014** on transparency, **Ley 1755/2015** on PQRSD) to:

- Register all official communications with a unique, immutable tracking number
- Maintain complete, auditable traceability of the document lifecycle
- Apply document retention schedules (TRD) and lawful final disposition
- Classify information by security level and enforce access control

**OrpycaMCP solves this as free software** — a production-ready alternative to expensive commercial DMS solutions.

---

## Quick Start

```bash
# Clone and start all services
git clone https://gitlab.com/orpyca/orfeo_mcp.git
cd orfeo_mcp
docker compose up -d

# View logs
docker compose logs -f api-gateway
```

**Entry points (development):**

| Surface | URL | Credentials (dev only) |
|---|---|---|
| API Gateway | http://localhost:19080 | — |
| API Docs (aggregated) | http://localhost:19080/docs | — |
| Keycloak Admin | http://localhost:19180/admin | `admin` / `orpycamcp_dev` |
| Object store (S3 API) | http://localhost:19900 | `orpycamcp` / `orpycamcp_dev` |
| MailHog (email capture) | http://localhost:8025 | — |
| PostgreSQL | `localhost:15432` | `orpycamcp` / `orpycamcp` (db `orpycamcp_db`) |

### Production deployment

See the **[Deployment Guide (ES)](docs/es/deployment.md)** / **[(EN)](docs/en/deployment.md)** for TLS, secrets management, backups, scaling, and monitoring.

```bash
cp infra/.env.example .env   # edit with your credentials
docker compose -f infra/docker-compose.prod.yml up -d
```

---

## Architecture

**11 FastAPI microservices** behind a single gateway. Every service follows the same layout (app factory · `asyncpg` pool · per-tenant migrations · Repository/Service layers · Redis Stream events). The frontend and MCP layer are clients of the gateway — they never touch the database directly.

| Service | Port | Responsibility |
|---|---|---|
| **api-gateway** | 8080 | Central proxy, JWT validation, rate limiting, OpenAPI aggregation |
| **auth-service** | 8001 | Keycloak integration, RBAC (resolved in DB), URD, security clearance, immutable audit |
| **tenant-service** | 8002 | Institutions, dependencias (org tree), catalogs, parameters, holidays/business days |
| **document-service** | 8003 | Radicación E/S/I, metadata (JSONB), drafts, anulación, quick reply, PQRSD, IMAP intake, postal |
| **archive-service** | 8004 | Expedientes, TRD/CCD, electronic index, transfers, physical archive (FUID) |
| **storage-service** | 8005 | Object-store upload/download, SHA-256 checksums, versioning, preservation (plan + PREMIS) |
| **workflow-service** | 8006 | Routing flows, rules engine, append-only history, returns, sequential approvals (vistos buenos) |
| **notification-service** | 8007 | Email/alerts, Redis Stream consumers, signed outbound webhooks (HMAC) |
| **signature-service** | 8008 | Native electronic signature (SHA-256 + identity + timestamp); PKI/XAdES pluggable |
| **mcp-server** | 8009 | MCP layer: declarative tool catalog over the gateway (stdio + HTTP), per-permission scope |
| **knowledge-service** | 8011 | Knowledge layer (pgvector): semantic retrieval with **server-side ACL pre-filter** |

Supporting infrastructure: **PostgreSQL** (one schema per tenant), **Redis** (event streams), **RustFS** (S3-compatible object store; replaced MinIO, see ADR-027), **Keycloak** (OIDC), **MailHog** (dev email).

A **frontend** (SvelteKit SSR, ADR-011/ADR-020) lives in `frontend/` and consumes only the gateway — currently a planning scaffold, implemented in its own phase.

### Core concepts

| Concept | Description |
|---|---|
| **Radicado** | Registered document with an immutable tracking number — types Entrada (E), Salida (S), Interno (I) |
| **Expediente** | Logical grouping of related radicados (`open → closed → transferred`) with an electronic index |
| **TRD / CCD** | Retention schedule + classification scheme: series ↔ subseries, two-phase retention, AGN disposition |
| **Flujo** | Distribution route between dependencias, with an append-only history |
| **Anexo** | File attachment in the object store, SHA-256 checksummed and versioned |
| **Firma** | Electronic signature (hash + identity + timestamp); index signing at closure |
| **FUID** | Formato Único de Inventario Documental — canonical inventory export |

### Tech stack

- **Framework**: FastAPI (async-first, built-in OpenAPI)
- **Data access**: `asyncpg` with **raw SQL — no ORM** (ADR-003); per-tenant migrations (ADR-012)
- **Database**: PostgreSQL 15 — multi-tenancy via **schema isolation** (`tenant_{slug}`, ADR-002)
- **Search**: **PostgreSQL FTS** (`tsvector` + `pg_trgm` + JSONB/GIN), not Elasticsearch (ADR-014)
- **Knowledge / AI**: **pgvector** embeddings, pluggable provider (ADR-006); MCP via the official SDK (ADR-019)
- **Object storage**: RustFS (S3-compatible; replaced MinIO after it archived its open-source project — ADR-027)
- **Auth**: Keycloak (OAuth2, JWT) — authentication in Keycloak, **authorization resolved in the DB** (ADR-013)
- **Events**: Redis Streams; signed outbound webhooks (HMAC-SHA256, ADR-018)
- **Quality**: `ruff`, `mypy`, `pytest` — all run inside Docker

### Multi-tenancy

Each institution gets its own **PostgreSQL schema** (`tenant_{slug}`): complete data isolation, per-tenant object-store buckets (`orpycamcp-{slug}-documents`), automatic schema provisioning, and `search_path` switched per request from the JWT claims.

### Security & access control

- **Authentication** via Keycloak (JWT); identity headers injected by the gateway.
- **Authorization** resolved in the database per request (ADR-013): RBAC (4-table model), URD (user–role–dependencia).
- **Immutable audit** — hash-chained `audit_log` per tenant (ADR-008).
- **Information classification** (Ley 1712/2014): documents and expedientes carry a security level (PUBLICA/RESERVADA/CLASIFICADA); **read access control** (search, listing, detail, and semantic retrieval) is enforced server-side by the caller's clearance — *fail-closed* to PUBLICA.

---

## Documentation

Full bilingual documentation (ES/EN) is built with MkDocs and published at **[orpyca.com](https://orpyca.com)**.

- **[Getting Started](docs/es/getting-started.md)** ([EN](docs/en/getting-started.md)) — setup and your first radicado
- **[API Reference](docs/es/api.md)** ([EN](docs/en/api.md)) — endpoints, schemas, status codes
- **[Domain Model](docs/es/domain.md)** ([EN](docs/en/domain.md)) — entities and business rules
- **[Architecture Decisions (ADR-001…020)](docs/es/adrs.md)** — what was decided and why
- **[Deployment](docs/es/deployment.md)** ([EN](docs/en/deployment.md)) · **[Roadmap](docs/es/roadmap.md)** ([EN](docs/en/roadmap.md))
- **[Contributing](CONTRIBUTING.md)** — workflow, code standards, PR process

Internal architecture notes for contributors live in [CLAUDE.md](CLAUDE.md).

---

## Status & roadmap

Two layers of planning: a **foundational scaffold** (8 services + infra + CI/CD, complete) elevated by the **specDrive plan** to **SGDEA compliance** (21 epics, 138 functional requirements). Core of all 21 epics is implemented, tested in Docker, with migrations validated against real PostgreSQL/pgvector. **20 ADRs accepted.**

| specDrive phase | Scope | Status |
|---|---|---|
| **F1 — Foundations** | common library, per-tenant migrations, administration, security (RBAC + audit + URD + classification), radicación, metadata, anexos, notifications, flows | ✅ |
| **F2 — Consultation** | advanced search + reports, public lookup by verification code, IMAP email intake | ✅ |
| **F3 — Lifecycle** | TRD/CCD, expediente closure/transfer/foliation, native electronic signature | ✅ |
| **F4 — Compliance** | electronic index (versioned XML, append-only, per-document fingerprint + verification) | ✅ core |
| **F5 — Preservation / physical** | physical archive (locations, units, signatura, loans, FUID), transfers, interoperability (signed webhooks), preservation (plan + PREMIS) | ✅ core |
| **F6 — Intelligence** | mcp-server (tool catalog over the gateway), knowledge-service (pgvector semantic retrieval with ACL) | ✅ core |

**Deferred** (depend on external infra/integrations): PKI/XAdES digital signature, WORM/Object-Lock + PDF/A validation, real embedding/LLM providers, postal-operator integration, frontend UI implementation, OAI-PMH/CMIS. See the [Roadmap](docs/es/roadmap.md) for detail.

---

## Development

### Prerequisites

- Docker 24.0+ and Docker Compose 2.20+
- Linux, macOS, or Windows with WSL2

> **Everything runs in Docker** — build, tests, and tooling. Nothing is installed on the host.

### Running tests

```bash
# Build then run a service's test suite (no external deps needed)
docker compose build document-service
docker compose run --rm --no-deps document-service pytest -q
```

### Before every change

- Update `docs/es/` (and `docs/en/`) when endpoints, schemas, or behavior change
- Add a `CHANGELOG.md` / `docs/es/changelog.md` entry (Keep a Changelog)
- Mark completed items in `docs/es/roadmap.md`
- Pass `ruff check`, `mypy`, and `pytest`

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full guidelines.

---

## Community

- **Issues**: [GitLab Issues](https://gitlab.com/orpyca/orfeo_mcp/issues)
- **Discussions**: [GitLab Issues](https://gitlab.com/orpyca/orfeo_mcp/discussions)
- **Contact**: aurigadl@gmail.com
- **License**: **AGPL v3** — see [LICENSE](LICENSE). Commercial support and custom development available by request.

---

## Acknowledgments

OrpycaMCP is built upon the legacy of the original [Orfeo GPL](https://github.com/orfeosgd/orfeo) system — respecting the business logic and domain knowledge accumulated over 20+ years of use in Latin American institutions.

Special thanks to the people who contributed ideas, domain expertise, and feedback that shaped this project:

- **Ricardo Perilla**
- **Juan Rodríguez**
- **Jairo Losada**
- **Giam Pieruccini**
- **Israel Pardo**
- **Edgar Buitrago**
- **Wilson Huérfano**
- **Hollman Ladino Paredes**
- **Liliana Gomez Velasquez**
- **William Duarte**
- **WCarlos Barrero**
- **Jose Rueda**
- **JFabian Losada**

And to the broader Orfeo community of archivists, developers, and public servants who keep free document management alive.

---

## Citation

If you use OrpycaMCP in research or production, please cite:

```bibtex
@software{orpyca-mcp,
  title  = {OrpycaMCP: Free Document Management System (SGDEA) for Latin America},
  author = {aurigadl},
  email  = {aurigadl@gmail.com},
  url    = {https://gitlab.com/orpyca/orfeo_mcp},
  license = {AGPL-3.0}
}
```

---

**Made with care for open-source document management in Latin America.**
