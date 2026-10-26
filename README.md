# Docuboto

> **SGDEA / document management** — fork of [OrpycaMCP](https://gitlab.com/orpyca/orfeo_mcp) (AGPL v3)
> FastAPI microservices · PostgreSQL · RustFS (S3) · Keycloak · Redis · SvelteKit

Docuboto keeps OrpycaMCP’s Orfeo-like domain (radicación, bandejas, TRD, expedientes, archivo, PQRSD, MCP) under the same **AGPL-3.0** license. See `NOTICE` and `LICENSE`.

Upstream: https://gitlab.com/orpyca/orfeo_mcp

---

## Quick start

```bash
cd docuboto
docker compose up -d
docker compose logs -f api-gateway
```

**Dev entry points** (same ports as OrpycaMCP; credentials still use the upstream `orpycamcp_*` defaults until you change `.env`):

| Surface | URL | Dev credentials |
|---|---|---|
| API Gateway | http://localhost:19080 | — |
| API Docs | http://localhost:19080/docs | — |
| Keycloak Admin | http://localhost:19180/admin | `admin` / `orpycamcp_dev` |
| Object store | http://localhost:19900 | `orpycamcp` / `orpycamcp_dev` |
| PostgreSQL | `localhost:15432` | `orpycamcp` / `orpycamcp` (db `orpycamcp_db`) |

Production: copy `infra/.env.example` → `.env`, then:

```bash
docker compose -f infra/docker-compose.prod.yml up -d
```

---

## Docs

Bilingual docs live under `docs/es/` and `docs/en/` (MkDocs). Architecture, ADRs, API, and roadmap are inherited from OrpycaMCP; update them as Docuboto diverges.

## License

AGPL-3.0 — same as OrpycaMCP / Orfeo lineage. If you run a modified Docuboto as a network service, you must offer corresponding source to users (AGPL §13).
