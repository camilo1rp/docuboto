# Production Deployment Guide

> **IMPORTANT**: This guide is a starting point. Consult with your institution's DevOps and security specialists before deploying to production.

## Table of contents

- [Prerequisites](#prerequisites)
- [Infrastructure configuration](#infrastructure-configuration)
- [Secrets and environment variables](#secrets-and-environment-variables)
- [Deployment with Docker Compose](#deployment-with-docker-compose)
- [Clustering and scalability](#clustering-and-scalability)
- [Backups and recovery](#backups-and-recovery)
- [Upgrades and rollback](#upgrades-and-rollback)
- [Monitoring and logs](#monitoring-and-logs)
- [Security](#security)
- [Troubleshooting](#troubleshooting)

---

## Prerequisites

### Minimum hardware

One figure, for an entity of **up to 50 staff** (ADR-053 §6):

**2 vCPU / 4 GB / 20 GB SSD**

plus whatever object storage the archive's growth needs (attachments and
preservation packages live there, not on the system disk). This is the only
hardware figure in the documentation: any other is a mistake.

A guard (`shared/tests/test_hardware_figure.py`) adds up the `deploy.resources.limits`
of the default profile in `infra/docker-compose.prod.yml` and fails if they exceed
this figure. **Today they do** (11 CPU / 16.25 GB of limits: no gateway or worker since ADR-047, but
Keycloak, Redis and the object store are still there): the figure is the redesign's target (ADR-048,
ADR-049, ADR-050), and the guard is marked as a strict expected failure until the
manifest fits. When it fits, the mark comes off.

### Software

- Docker 24.0+
- Docker Compose 2.20+
- Linux (RHEL, Ubuntu, Debian). Kubernetes is not supported yet (see [Option 2](#option-2-kubernetes-not-supported-yet))
- Valid TLS certificate (free Let's Encrypt or corporate CA)
- Resolved DNS domain

### Knowledge

- Linux/Docker administration
- Network security (firewall, VPN)
- PostgreSQL databases
- Backup and recovery

---

## Infrastructure configuration

### 1. Prepare the server

```bash
# Update the operating system
sudo apt update && sudo apt upgrade -y

# Install Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# Install Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# Create the data directory (outside the source tree)
sudo mkdir -p /srv/orpyca/{postgres,minio,redis,certs}
sudo chown $USER:$USER /srv/orpyca -R
```

### 2. Obtain TLS certificates

The `edge` service (containerised nginx, §4) mounts the server's `/etc/letsencrypt` read-only and
needs **one certificate per domain**: `FRONTEND_HOSTNAME`, `API_PUBLIC_HOSTNAME` and
`KEYCLOAK_HOSTNAME`. With Let's Encrypt (free, needs port 80 free and the three names resolving to the
server), the **first issuance** uses `--standalone` and runs **before the first `up`** (nobody holds
port 80 yet):

```bash
sudo apt install certbot
sudo certbot certonly --standalone -d app.yourdomain.com
sudo certbot certonly --standalone -d api.yourdomain.com
sudo certbot certonly --standalone -d keycloak.yourdomain.com
# Certificates in /etc/letsencrypt/live/<domain>/{fullchain,privkey}.pem
```

**Renewals** can no longer use `--standalone` (`edge` owns port 80): they go through *webroot* on
`infra/certbot-www` (which `edge` serves at `/.well-known/acme-challenge/`) and reload nginx when done:

```bash
mkdir -p infra/certbot-www
sudo certbot renew --webroot -w "$PWD/infra/certbot-www" \
  --deploy-hook "docker compose -f $PWD/infra/docker-compose.prod.yml --env-file $PWD/infra/.env exec edge nginx -s reload"
```

With a corporate CA: leave each domain's `fullchain.pem` and `privkey.pem` in
`/etc/letsencrypt/live/<domain>/` (or mount your own path in the `edge` service).

### 3. Configure the firewall

```bash
# Assumes UFW on Ubuntu
sudo ufw default deny incoming
sudo ufw default allow outgoing

# Allow SSH (CRITICAL: do not lock yourself out)
sudo ufw allow ssh

# Allow the entry proxy (the `edge` service)
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

sudo ufw enable
```

> **Docker bypasses ufw for the ports it publishes.** A `ports:` entry in the compose file opens the
> port in `iptables` ahead of ufw's rules, so `ufw deny 5432` does **not** protect a published
> PostgreSQL. The real defence is not publishing them: the production manifest publishes **only**
> `80` and `443`, and only on the `edge` service; a guard
> (`shared/tests/test_manifiesto_prod_seguro.py`) fails if any other service publishes a port. Check
> from outside with `scripts/preflight-prod.sh`.

### 4. Production entry: the `edge` service (containerised nginx)

Entry is handled by the `edge` service in `infra/docker-compose.prod.yml` (ADR-033): containerised
nginx, **the only service with `ports:`** (`80:80`, `443:443`). nginx is not installed on the server.
The template is `infra/nginx/orpyca.conf.template`; the official image substitutes
`${FRONTEND_HOSTNAME}`, `${API_PUBLIC_HOSTNAME}` and `${KEYCLOAK_HOSTNAME}` at startup, so there is
**nothing to copy or edit**: the three domains come from the same `infra/.env`.

Why containerised rather than on the server:

- With nginx on the host, the app would see the docker-proxy IP as its peer (see `TRUSTED_PROXIES`,
  below).
- The app and the SSR call `https://${KEYCLOAK_HOSTNAME}` from inside the network: `edge` carries that name (and
  the other two) as **aliases** on `orpycamcp-net`, so Docker DNS resolves it to `edge` itself, with a
  valid certificate, no hairpin NAT and no external DNS. That is what makes the SSR's PKCE login work
  (`INTERNAL_KEYCLOAK_URL`).

What each domain publishes:

| Domain | Target | Note |
|---|---|---|
| `ORPYCA_REGISTRY` / `ORPYCA_VERSION` | Image registry and the version to deploy (an `X.Y.Z` tag; never `latest`). **Mandatory** (`:?`) in `docker-compose.prod.yml`; `release-<version>.yml` no longer uses them because it pins every image by digest | `ghcr.io/camilo1rp/docuboto` / `1.0.0` |
| `FRONTEND_HOSTNAME` | `frontend:3000` | HSTS; the SSR emits the other headers |
| `API_PUBLIC_HOSTNAME` | `app:8000` | `/metrics` answers 404; `/health/ready` only from the `edge` container itself (`127.0.0.1`); probe it with `docker exec <edge> wget --no-check-certificate -qO- --header 'Host: <API_PUBLIC_HOSTNAME>' https://127.0.0.1/health/ready`. The network gateway's IP (`172.20.0.1`) is **not** allowed: it is what nginx sees for any internet client |
| `KEYCLOAK_HOSTNAME` | `keycloak:8080` | **allow-list**: only `/realms/orpycamcp/` and `/resources/`; everything else (including `/admin/`, `/realms/master`, `/realms/master;x/…`) answers 404, and so does any path containing `;` |

**Rate limiting (ADR-047).** The per-IP bucket lives in `edge` (nginx `limit_req`, in memory, no
Redis), with the budgets the retired api-gateway had:

| Zone | What it covers | Limit per IP |
|---|---|---|
| `api_ip` | everything | 1200/min (`20r/s`) |
| `login_ip` | `/api/v1/auth/token` and `/refresh`, and the SSR's `/login` | 20/min |
| `radicacion` | anonymous filings: `POST` to the window/PQRS and to the SSR's public form (`/radicacion`) | 5/min, `POST` only |
| `cosecha_ip` | public OAI-PMH and CMIS | 30/min |

Over the limit, nginx answers `429`. nginx doesn't know who anyone is, and a whole office behind a
NAT shares one IP, so the app enforces the **per-person quota** (`UserRateLimitMiddleware`,
`RATE_LIMIT_PER_USER`, 120 requests per minute by default; `429 rate_limit_exceeded` with
`Retry-After: 60`). The window lives in process memory. Requests without a person don't count
there: the per-IP bucket covers them.

Keycloak's admin console is **not published**: use it through an SSH tunnel
(`ssh -L 8080:localhost:8080 server`, towards the container's port) or `docker compose exec keycloak`.

```bash
cd infra
docker compose -f docker-compose.prod.yml --env-file .env config -q   # validate the manifest
docker compose -f docker-compose.prod.yml --env-file .env up -d
docker compose -f docker-compose.prod.yml --env-file .env exec edge nginx -t
```

### Verified IP on the public window (`TRUSTED_PROXIES`)

The window's authorization record (Law 1581/2012, ADR-031) stores the client IP **only if the app can vouch for it**. `UsuarioActualMiddleware` derives it once per request by walking `X-Forwarded-For` **from the right**, dropping the `TRUSTED_PROXIES` hops, and stores it in `request.state`; no domain reads `X-Forwarded-For` again. For `ip_verificada` to be `true` in production:

1. The app's `TRUSTED_PROXIES` must include **the `edge` network and the SSR frontend's** (the two hops that chain `X-Forwarded-For`: nginx uses `proxy_add_x_forwarded_for` and the SSR forwards the browser's IP). The manifest default is **`172.20.128.0/17`**: the `ip_range` of `orpycamcp-net` (the subnet is `172.20.0.0/16`, but containers only get addresses from its upper half). If one is missing, the app sees the proxy itself as the client: the record shows the proxy's IP, not the person's.

   **Why the network's gateway (`172.20.0.1`) is left out.** nginx *appends* `$remote_addr` to whatever the client sent in `X-Forwarded-For`. If `$remote_addr` ever were `172.20.0.1` (an IPv6 client served through docker-proxy, or a process on the server itself) and that IP were trusted, the app would skip it and keep the entry **forged by the client**, which would end up sealed in `audit_log` and in the record (ADR-031). With the network's gateway outside the trusted range the outcome is *fail-closed*: `172.20.0.1` is recorded, never an invented IP. A guard (`test_trusted_proxies_excluye_el_gateway_de_la_red`) prevents going back to a `/16`. If you change the network's subnet in the compose file, change `TRUSTED_PROXIES` with it.

   **IPv6.** Docker delivers IPv6 clients through its docker-proxy, which hides the real IP (you would see `172.20.0.1`). To keep it, enable `"ip6tables": true` in `/etc/docker/daemon.json`; without it an IPv6 client's IP shows as `172.20.0.1` (uninformative, but it cannot be forged).
2. With `TRUSTED_PROXIES` empty (the default) the header is ignored and the socket address is used: safe, but uninformative.

   **The app refuses to start if it is left that way (ARC-B4).** Its `lifespan` checks it (the api-gateway used to). The production manifest sets `APP_ENV=production` and `GATEWAY_RED_DOCKER_GATEWAY=172.20.0.1` (it must equal the network's `gateway:`; a guard checks it); if `TRUSTED_PROXIES` covers that IP, startup ends with an error that explains it. If the variable is empty, the IP is derived from the container's default route. **Production only:** in development `docker-compose.yml` sets `172.16.0.0/12` (which does contain it) on purpose and is not checked.

**Important:** the IP is only reliable if the frontend is reachable **only through `edge`**. An SSR port exposed directly (in development, `19300` with `XFF_DEPTH=1`) accepts a client-forged `X-Forwarded-For` and chains it as if trusted; in production no service except `edge` publishes ports (manifest guard).

If your `infra/.env` comes from an earlier version and sets `TRUSTED_PROXIES=172.20.0.0/16`, **change it** to `172.20.128.0/17` (or delete it): with the `/16` the network's gateway becomes trusted again.

Without a usable IP the record says `ip=null, ip_verificada=false` and filing is **not** blocked.

### Each entity's data-processing policy

The public window **does not file** until the entity publishes its privacy notice (409 `politica_no_publicada`): set `politica_tratamiento_version`, `politica_tratamiento_url` and/or `politica_tratamiento_texto` in `/admin/parametros` (level-3 administrator). Start from the [template](politica-tratamiento-plantilla.md). `./scripts/verificar-ventanilla.sh <slug>` exits non-zero when it is missing (`SIN POLITICA`).

---

## Secrets and environment variables

### CRITICAL! Secure handling of secrets

**NEVER** commit `.env` to Git. Each environment must have different secrets.

```bash
# 1. Create the .env file on the server (local, never tracked)
cd /srv/orpyca
cp infra/.env.example .env

# 2. Generate strong passwords
openssl rand -base64 32  # PostgreSQL password
openssl rand -base64 32  # Redis password
openssl rand -base64 32  # object-store password
openssl rand -base64 32  # Keycloak admin password

# 3. Edit .env with the real values
nano .env
```

### Required variables

| Variable | Description | Example |
|---|---|---|
| `INTELLIGENCE_CREDENTIAL_KEK` | KEK (base64 of 32 bytes) that encrypts the intelligence service client's secret. **`init_tenant.py` will not create an entity without it**, with or without the intelligence overlay (ADR-053 §5) | `<openssl rand -base64 32>` |
| `DB_USER` | PostgreSQL user | `orpycamcp_prod` |
| `DB_PASSWORD` | PostgreSQL password (32+ characters) | `<openssl rand -base64 32>` |
| `REDIS_PASSWORD` | Redis password (32+ characters) | `<openssl rand -base64 32>` |
| `MINIO_ROOT_PASSWORD` | Object-store root password (the `MINIO_` prefix is historical, ADR-027) | `<openssl rand -base64 32>` |
| `KEYCLOAK_HOSTNAME` | Keycloak FQDN domain | `keycloak.yourdomain.com` |
| `KEYCLOAK_ADMIN_PASSWORD` | Keycloak admin password | `<secure password>` |
| `KEYCLOAK_AUTH_SERVICE_SECRET` | Secret of the app's confidential OAuth2 client | `<32 char hex>` |
| `FRONTEND_HOSTNAME` | Public FQDN of the SSR frontend (defines `ORIGIN` and the login `redirect_uri`) | `app.yourdomain.com` |
| `API_PUBLIC_HOSTNAME` | Public FQDN of the API (the app, behind `edge`) as the browser sees it | `api.yourdomain.com` |
| `SIGNATURE_SECRET` | HMAC key for signature sealing (E06). Random, rotate it. **The app refuses to start without it** | `<openssl rand -base64 32>` |
| `MINIO_ROOT_USER` | Object store user. **The app refuses to start without it** | `orpyca_prod` |
| `SMTP_HOST` | SMTP host | `smtp.gmail.com` |
| `SMTP_PASSWORD` | SMTP password or application token | `app-specific-password` |

**There are no internal tokens to generate (ADR-047).** Calls between domains are function calls
inside one process: `X-Internal-Token`, every `*_INTERNAL_TOKEN`, every `*_SERVICE_URL`,
`IDENTITY_SIGNING_SECRET`, `AUTH_CACHE_MAC_SECRET` and `KEYCLOAK_API_GATEWAY_SECRET` are gone. If your
`infra/.env` comes from an earlier version, delete them: nothing reads them. The other variable names
don't change; the app reads all of them from `docuboto/config.py`.

### App variables (optional)

| Variable | Default | What it does |
|---|---|---|
| `HEAVY_JOBS_CONCURRENCY` | `1` | Slots for heavy work (veraPDF, format migration, LTA renewal, the `worker` queue of `trabajo_fuera`). They share one semaphore inside the process; raise it only if the machine has spare CPU |
| `RATE_LIMIT_PER_USER` | `120` | Requests per person per minute (§4, rate limiting) |
| `FORMAT_MIGRATION_ENABLED` | `true` in the manifests | Loop that migrates obsolete formats; runs in the app (it used to run in the `worker` container) |
| `TRUSTED_PROXIES` | `172.20.128.0/17` in prod | Hops allowed to write `X-Forwarded-For` (see above); the app reads it |

### Variables that block startup when missing

Three secrets used to ship with a **working default** published in an AGPL
repository: `SIGNATURE_SECRET` was `change-me-in-production`, and the object-store
credentials were the development ones printed in `CLAUDE.md` itself. A deployment
that forgot to set them **did not fail** — it started up and signed (or stored
official attachments) with a key anyone can read on GitHub.

The defaults are now empty strings **and** startup checks them: the app refuses to
serve without `SIGNATURE_SECRET`,
`MINIO_ACCESS_KEY` and `MINIO_SECRET_KEY`. Example placeholders
(`change-me-in-production` and friends) are rejected too, in case someone copies
`.env.example` untouched. The error message names the variable, never the value
received.

**`ALTCHA_HMAC_KEY` (C16, ADR-038) is required**: a key (≥ 32 bytes; `openssl rand -hex 32`) with which the app signs the anti-bot challenges of the anonymous public window. The production manifest demands it (`${ALTCHA_HMAC_KEY:?}`) and the app **does not start** without it or with fewer than 32 bytes. It never reaches the browser; rotating it only invalidates live challenges (≤ `ALTCHA_TTL_SEGUNDOS`, default 300). `ALTCHA_MAXNUMBER` (100000) caps the client's cost. If Redis goes down, the window answers `503 antirrobots_no_disponible` (fail-closed: without Redis the single use of a challenge cannot be guaranteed).

If you use email ingestion (E19), `EMAIL_ENCRYPTION_KEY` is required: mailbox
credentials are encrypted at rest with it. There are no `IMAP_*` variables any
more — each mailbox is configured from `/admin/correo` and lives in
`email_cuenta`. For automatic polling, `EMAIL_SONDEO_TENANT_SLUG` declares
**which institution owns the mailboxes the process polls**; without it polling
does not start even when `EMAIL_SONDEO_ENABLED` is on.

### CORS

`CORS_ALLOW_ORIGINS` is **empty by default and the middleware is not installed at
all**. This is not a regression: the frontend talks to the API **from the
server** (SSR), not from the browser, so a typical deployment needs no CORS. Only
declare it for a third-party integration or an SPA on another domain:

```bash
CORS_ALLOW_ORIGINS=https://sgd.entidad.gov.co,https://portal.entidad.gov.co
```

> The `*` wildcard is accepted but **disables credentials**. The previous setting
> was `allow_origins=["*"]` **together with** `allow_credentials=True`, hardcoded:
> with credentials Starlette cannot answer `*`, so it reflects whatever `Origin`
> arrives — any page on the internet could call the API with the visitor's
> credentials.

### Audit log partitions

`public.audit_log` has been declared month-partitioned since its first migration,
but the job that creates the partitions never existed: everything piled up in
`audit_log_default`. Beyond the performance cost, the trap tightens over time —
PostgreSQL **refuses** to create a partition whose range overlaps rows already
sitting in the default one.

Migration `005_audit_partition_job.sql` installs `ensure_audit_partitions(n)` and
runs it once. Schedule it monthly:

```bash
# Creates (and hardens) the next 3 months of partitions. Idempotent.
docker compose -f infra/docker-compose.prod.yml run --rm --no-deps \
    -v "$PWD/scripts:/scripts:ro" app \
    python /scripts/audit_partitions.py --meses 3

# For monitoring: exit 0 if up to date, 1 if next month's partition is missing.
... python /scripts/audit_partitions.py --verificar
```

It runs several months ahead on purpose: if the cron fails silently, there is
slack before entries start landing in the default partition again.

**A cron failure is visible without reading any log.** The app's
`GET /health/ready` applies the same logic as `--verificar` (does next month's partition
exist?) read-only and with the application role: if it is missing it answers **200** with
`"degraded": ["audit_particiones"]` and `"audit_particiones": "degraded"` (an operations
alert, it does not pull the app out of the balancer); if it cannot query, `unknown`
and it does not accuse. Alert on that field in the monitoring you already have.

### Daily audit-chain verification

`verify_chain` only ran on demand (`GET /api/v1/audit/verify`) or on restore. Schedule it
**daily**, with the module that `backup.sh` and `restore.sh` also use:

```bash
# Exit 0 if ALL chains are intact and none went backwards against the last anchor; 1 on a
# break, a regression or an unreadable anchor. Read-only.
docker compose -f infra/docker-compose.prod.yml run --rm --no-deps -T \
    -e "DATABASE_URL=postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@postgres:5432/${DB_NAME}" \
    app python -m auth.ops.verify_audit_chains \
    --anclas - < /srv/orpyca/backups/anclas-auditoria.tsv
```

It walks `public.tenants`, every `tenant_slug` present in `audit_log` (including
`__system__`) and those in the anchor; for each it recomputes the chain, requires the
count not to drop and the anchored entry's hash not to change. **`backup.sh` invokes it
before anchoring and does not anchor if it fails** (fail-closed: sealing an already
broken chain as a "trusted end point" is worse than not anchoring), **but the backup
still completes**: it is encrypted, sealed with `verificacion_auditoria=FALLIDA` and the
script exits **3** at the end (alert on 3 and 4). `audit_log` is append-only, so a
historical break can never be repaired: the responsible officer **declares** it in a
baseline (`BACKUP_AUDIT_ROTURAS_DECLARADAS`, lines `ROTURA<TAB>slug<TAB>id<TAB>note`) and
only a NEW break, a regression or a changed anchored hash fail again. If the object-lock
retention could not be read completely the seal carries `retencion_capturada_completa=NO`
and the script exits **4**; `restore.sh` refuses that backup unless
`RESTORE_ACEPTAR_RETENCION_INCOMPLETA=1`. A previous version under live COMPLIANCE fails
the restore unless `RESTORE_ACEPTAR_PERDIDA_VERSIONES=1`; the restore is recorded by
`auth.ops.registrar_restauracion` (`storage.restauracion` audit entry plus
`preservacion_worm_objeto.version_id` update, one transaction). `restore.sh` reuses it instead of the previous heredoc.
In development, `BACKUP_AUDIT_EXCLUIR_PREFIJOS="itest"` leaves out the disposable tenants
the integration tests left behind (the record is append-only and cannot be deleted);
**do not set it in production**.

> History already in `audit_log_default` is **not moved**. Rewriting rows of an
> append-only, hash-chained record means touching the evidentiary basis of the
> whole archive, and tidying up old data does not justify it. The function starts
> at the first non-overlapping month: the default freezes and stops growing.

### Issuer (`iss`) contract — critical

The tokens' `iss` is **always the public URL** through which the browser logs in. The
app, which validates the JWTs, must expect exactly
that `iss` or it will reject **every** token (fail-closed).

- **Production (single host):** Keycloak runs with `--hostname-strict=true
  --hostname=${KEYCLOAK_HOSTNAME}`, so the browser and the services use the same host.
  `expected_issuer` is derived from `KEYCLOAK_URL` and **no override is needed**.
- **Development (`docker-compose.yml`):** there is a split (browser `localhost:19180`
  ↔ internal network `keycloak:8080`); that is why `KEYCLOAK_ISSUER` is set there with
  the realm's public URL.

On startup, the app emits a **fail-fast WARNING** if `KEYCLOAK_URL` points at an
internal Docker host without `KEYCLOAK_ISSUER` defined (a configuration that would
reject every token). After deployment, check that it doesn't appear:

```bash
docker compose -f docker-compose.prod.yml logs app | grep "esperado sera"
```

### SSR frontend

The frontend (SvelteKit + adapter-node) runs in its **own container** behind the
reverse proxy. It needs to resolve the `origin` correctly in order to build the PKCE
login `redirect_uri`:

- `ORIGIN=https://${FRONTEND_HOSTNAME}` (or `PROTOCOL_HEADER=x-forwarded-proto` +
  `HOST_HEADER=x-forwarded-host` if the proxy injects them).
- **Public** URLs (browser): `PUBLIC_API_URL`, `PUBLIC_KEYCLOAK_URL`.
- **Internal** URLs (SSR → Docker network): `INTERNAL_API_URL`, `INTERNAL_KEYCLOAK_URL`.

The resulting `redirect_uri` (`https://${FRONTEND_HOSTNAME}/login/callback`) must be
listed in the `redirectUris`/`webOrigins` of the `orpycamcp-frontend` client in
Keycloak.

### Secret rotation

Every 90 days (or per corporate policy):

```bash
# 1. Generate a new password
NEW_PASSWORD=$(openssl rand -base64 32)

# 2. Update it in PostgreSQL
docker compose exec postgres psql -U orpycamcp -c "ALTER USER orpycamcp PASSWORD '$NEW_PASSWORD';"

# 3. Update .env and restart the services
sed -i "s/DB_PASSWORD=.*/DB_PASSWORD=$NEW_PASSWORD/" .env
docker compose restart app
```

---

## Deployment with Docker Compose

### Which containers run (ADR-047)

Docuboto is **one process**: the `app` container (image `infra/Dockerfile.app`) serves the whole API,
with the nine domain packages in the same `FastAPI`, and runs the event consumers and background
loops (format migration, LTA renewal, `trabajo_fuera` queues) in that same process. The image carries
JRE, veraPDF and Ghostscript for PDF/A validation. There is no `api-gateway`, no `worker` container
(`infra/Dockerfile.worker` is gone), no `split` profile and no E2E stack.

| Container | Production (`infra/docker-compose.prod.yml`) | Development (`docker-compose.yml`) |
|---|---|---|
| `migrate-docuboto` | yes, one-shot (`app` image) | yes, one-shot |
| `postgres` | yes | yes (`127.0.0.1:15432`) |
| `redis` | yes | yes (`127.0.0.1:16379`) |
| `minio` (RustFS) | yes | yes (`19900`) |
| `keycloak` | yes | yes (`19180`) |
| `app` | yes | yes (`127.0.0.1:19000`) |
| `frontend` | yes | yes (`19300`) |
| `edge` (nginx) | yes, the only one with `ports:` | no |
| `mailpit` | no | optional (own profile) |

**ADR-047's target —four containers: `app`, `postgres`, `edge`, `frontend`— isn't reached yet.**
Keycloak, Redis and the object store stay in the manifest until ADR-048 (own identity), ADR-049
(events in PostgreSQL) and ADR-050 (bytes on disk) retire them.

### Using docker-compose.prod.yml

```bash
# 1. Clone the repository
git clone https://github.com/camilo1rp/docuboto.git /srv/orpyca
cd /srv/orpyca && git checkout 1.0.0

# 2. Create the directory structure
mkdir -p infra/certs infra/logs

# 3. Configure the environment variables
cp infra/.env.example infra/.env
nano infra/.env  # Editar con valores reales

# 4. Bring the stack up
cd infra
docker compose -f docker-compose.prod.yml up -d

# 5. Check the status
docker compose ps
docker compose logs -f app

# 6. Create the entity (once Keycloak is up)
#    The script runs ON THE HOST, not inside a container, and takes its three
#    arguments positionally: <slug> <name> <code>.
cd ..
COMPOSE_FILE=infra/docker-compose.prod.yml ENV_FILE=infra/.env \
  ./scripts/init-tenant.sh my-institution "My Institution" MYINST
```

**One installation, one entity (ADR-047 §3).** `public.tenants` is the configuration row of the
installation's entity: it has no lifecycle states (`status`, `provisioning_status`), there is no
HTTP entity registry and no suspended-entity veto. The `tenant_{slug}` schema stays. Two declared
leftovers: buckets are still named per entity (`orpycamcp-<slug>-documents`; ADR-050 retires them)
and `init_tenant.py` still creates the Keycloak intelligence client (ADR-048 replaces it).

**Personal signing certificate.** After you issue a person's certificate offline with
`scripts/gen-user-cert.sh`, load it inside the container (there is no internal HTTP route any more):

```bash
docker compose exec -T app python -m auth.ops.activar_certificado \
  --tenant <slug> --user-id <uuid> --cert - < user.crt
```

### Post-deployment verification

```bash
# Review each service's logs
docker compose logs -f postgres      # Connection errors
docker compose logs -f keycloak      # OIDC/realm issues
docker compose logs -f app           # Application errors

# Try the health check (through Nginx if it is configured)
curl -k https://keycloak.yourdomain.com/health

# Verify the object store is running
#
# There is no `mc` client inside the container any more: the engine is RustFS
# (ADR-027) and MinIO pulled its client from the registries too. Use `aws-cli`.
docker compose exec minio curl -fsS http://localhost:9000/health

# Verify Redis is running
docker compose exec redis redis-cli -a $REDIS_PASSWORD ping
```

#### The two probes: `/health` and `/health/ready`

The app exposes **two** routes, and they are not interchangeable:

| Route | Question | Checks | Consumed by |
|---|---|---|---|
| `/health` | Is the process answering? | Nothing | The container `healthcheck` |
| `/health/ready` | Can it serve traffic **right now**? | Decides on database and Redis; reports, without deciding, Keycloak, object store, SMTP, next month's `audit_log` partition and each event consumer with its DLQ | The load balancer / reverse proxy |

`/health` **depends on nothing, deliberately**. Restarting the app because
Postgres is down does not fix Postgres: it discards in-flight work and adds a
restart storm to an ongoing incident. `/health/ready` returns **503** when the
app cannot work, which is the signal for the balancer to stop sending it
requests **without restarting it**.

Configure the reverse proxy to drop the app when `/health/ready` answers 503;
leave restarts to `/health`.

```bash
# Aggregate diagnosis (only from the edge container itself, see §4)
docker compose exec edge wget --no-check-certificate -qO- \
  --header "Host: $API_PUBLIC_HOSTNAME" https://127.0.0.1/health/ready
```

In `consumers`, the value `reiniciando` for a consumer means it died and is
retrying: the app answers requests, but what that consumer does —routing
incoming mail, sending email, sealing signatures, feeding the search index— **is
not happening**. Worth investigating even while the app shows as available.

Probes report state as a single word (`ok`, `error`, `unreachable`) and never the
failure detail: they are reachable without authentication and the text of a
network exception carries internal hostnames and ports. The real reason is in the
app log.

---

## Clustering and scalability

For large institutions (> 10,000 users):

### Option 1: Docker Swarm (simple)

```bash
# Initialise the cluster
docker swarm init
docker swarm join --token <TOKEN> manager-node-ip

# Deploy the stack
docker stack deploy -c docker-compose.prod.yml orpyca-mcp
```

### Identity and `search_path` (ADR-044, ADR-047)

Identity is resolved **once per request**: `UsuarioActualMiddleware` validates the JWT, puts the person
in `request.state` and strips the identity headers the client sends (`X-User-Id`, `X-Username`,
`X-User-Roles`, `X-Canal`, `X-Tenant-Slug`; it resets the last one from the token's slug). With no
network hops between domains there is no header to sign: ADR-047 retired the nonce-signed identity
(`IDENTITY_SIGNING_SECRET`, `X-Identity-Nonce`), and with it Redis as a hard dependency of every
authenticated request.

The `search_path` of every tenant connection is **only** the `tenant_{slug}` schema. The extensions
(`pg_trgm`, `unaccent`) **must live in `public`**; `migrations/global/010_extensions.sql` creates them.

### Option 2: Kubernetes (not supported yet)

**The repository ships no Kubernetes manifests or Helm charts** (no `Chart.yaml`, no
`kustomization`), and automatic conversion from Compose has not been tested: the `migrate-docuboto`
containers, the `service_completed_successfully` dependencies and file-based secrets do not translate
by themselves. The supported route is Docker Compose (Option 1) or Swarm. If you scale replicas on any
orchestrator, **review the in-memory state first**: the per-person quota (`RATE_LIMIT_PER_USER`), the heavy-work
semaphore (`HEAVY_JOBS_CONCURRENCY`) and other caches are per process, and the background loops run in
every `app` replica.

### Scalability

This manifest runs **one replica** of `app` and of `frontend`. One entity and about 10–15
concurrent users fit in a single process. The app opens **one pool** of `DB_POOL_MAX_SIZE=10`
connections; Keycloak, `KC_DB_POOL_MAX_SIZE=30` against the same Postgres (40 in total, plus headroom
for migrations, backups and `psql`). Keycloak is capped at 1 GB.

With one process you can't replicate only the slow domain (ADR-047); at the target customer's volume
you don't need to.

---

## Backups and recovery

> **The scripts exist and have been exercised.** `infra/backup.sh` and
> `infra/restore.sh` are executable files in the repository, not snippets to copy
> and paste. The previous version of this section was a bash block that had **never
> been run**: it used the development database user, mirrored the object store to a path no
> container mounts using a client that image does not ship, lacked
> `set -euo pipefail` —so a failed dump produced an empty but valid `.gz`— and
> pruned backups older than 30 days **regardless**. In cron, at 2 a.m., that is a
> rotation of empty backups that looks perfectly normal. And the recovery procedure
> ran `docker compose down` and then `docker compose exec postgres`, against a
> container that no longer existed.

### Backup

```bash
ENV_FILE=infra/.env BACKUP_DIR=/srv/orpyca/backups ./infra/backup.sh
```

Dumps PostgreSQL (every schema: `public`, holding the entity's row and the
`audit_log`, plus one `tenant_*` per institution) and mirrors the object store. Three
guarantees worth knowing because they change how you operate it:

1. **The dump is verified before it is accepted** (`pg_restore --list` parses the
   header and the full index). A truncated file fails here, not six months later.
2. **Old backups are pruned only if that verification passed.** A backup that
   cannot be read does not authorise deleting the previous one.
3. An interrupted backup is renamed to `*.PARCIAL` and carries no `OK` seal file.
   `restore.sh` refuses any directory without that seal.
4. **The audit chain is verified before it is anchored** (see "Daily audit-chain
   verification"): a backup with a broken or regressed chain **fails** and writes no anchor.
5. **The object store retention** (Object-Lock) is saved for **every version** of every
   object —the exact `RetainUntilDate` instant and the legal hold (`LegalHold`)— in
   `minio-retencion.tsv`.

`custom` format (`-Fc`): already compressed, allows selective and parallel restore,
and —what makes point 1 possible— can be inspected without restoring it.

In cron, redirected to a log **someone actually reads**:

```bash
0 2 * * * ENV_FILE=/srv/orpyca/infra/.env /srv/orpyca/infra/backup.sh \
            >> /var/log/orpyca-backup.log 2>&1
```

### Secrets bundle (ADR-053 §5)

The dump and the object store are useless without the keys that read them:
`infra/.env` (every KEK; losing `SIGNING_KEY_ENCRYPTION_KEY` makes the held
private signing keys unrecoverable) and `secrets/signing`, `secrets/tsa` and
`secrets/ca-users-public`. They go into a **sealed bundle**, `gpg --symmetric`
(AES-256) with a password the operator keeps **off the server**:

```bash
ENV_FILE=infra/.env BACKUP_DIR=/srv/orpyca/backups ./infra/seal-secrets.sh
```

It asks for the password twice (16 characters minimum) or reads it from
`SECRETS_PASSPHRASE_FILE`; it writes `secrets-<date>.tar.gpg` to
`$BACKUP_DIR/secrets/` and opens it again before accepting it. A cron job cannot
type a password that is not on the server, so **sealing is manual**: at install
time and after every secrets change (a new entity with its seal, the TSA, a KEK
rotation, any `.env` change).

Every `backup.sh` run copies the latest bundle into the backup (`secrets.tar.gpg`)
and writes `secretos=OK|AUSENTE|DESACTUALIZADO` into the seal. If it is missing or
any secret is newer than it, the backup still completes but **exits with 6**. The
weekly drill fails when the latest backup lacks a current bundle; with
`SECRETS_PASSPHRASE_FILE` it also opens it and compares it file by file with the
live secrets.

To recover it on a new server, from the project root:

```bash
gpg --pinentry-mode loopback --decrypt secrets-<date>.tar.gpg | tar -xf -
sha256sum -c MANIFEST && rm MANIFEST
```

### Restore

```bash
RESTORE_OPERADOR="First Last" ENV_FILE=infra/.env ./infra/restore.sh /srv/orpyca/backups/20260808_020000
```

`RESTORE_OPERADOR` is **mandatory** (the person running the restore; the script exits before asking for confirmation or stopping anything if it is missing): the `storage.restauracion` record in `audit_log` is attributed to `operador:<name>` and declares, per tenant, the **lost-trail window** —the end of `audit_log` (count, id and hash of the last entry) is noted *before* `pg_restore` and compared with the restored one—. Restoring a backup older than the live state lawfully drops the later entries; the record says which `(from_id, to_id]` and how many.

Stops the application services and Keycloak but **leaves Postgres and the object store
running**, since they are the target of the work. It requires a typed
confirmation: restoring destroys the current state and is irreversible.

**The step that makes this a tested restore** is the last one: it recomputes the
`audit_log` **hash chain** per tenant and checks each entry against its predecessor.
A database that starts proves nothing about the integrity of the legal trail, which
is exactly what an archive must be able to demonstrate. If the chain is broken, the
script reports the `id` of the first affected entry.

**Retention is restored and counted (fail-closed).** After syncing the objects,
`restore.sh` restores the time-based retention and the legal hold of the current version
of every object, and **exits with an error** if a single valid retention or active legal
hold is left unrestored (deletable objects behind a green message is the defect this
prevents). An already expired retention is left as is. **Previous** versions with
retention cannot be restored (the mirror only copies the current version) and are
**reported**. Since after a restore the `version_id` in `preservacion_worm_objeto` no
longer matches any object, the script writes `mapa-version-id-<date>.tsv`, next to the
backups, with `OBJETO <bucket/key> <old version> <new version>`: the list for the record
that ADR-027 requires.

**Protected previous versions are backed up and restored (B4).** Besides the mirror,
`backup.sh` copies the content of each **previous** version under live COMPLIANCE or legal
hold into `minio-versiones/<bucket>/<sha256(key+version)>` and lists it in
`minio-versiones.tsv` (a separate file; `minio-retencion.tsv` keeps its format); it travels
inside the encrypted tar and the seal carries `versiones_previas_capturadas=SI|NO` (**exit 5**
if incomplete). Before the `sync`, `restore.sh` uploads them oldest first, verifies the
sha256, restores retention on the new `VersionId` and explicitly `put-object`s the current
version of those keys; the old→new map includes them and `registrar_restauracion` writes a
PREMIS `REPLICA` event (`motivo=restauracion`) per remapped object in the same transaction.
`RESTORE_ACEPTAR_PERDIDA_VERSIONES=1` remains only for older backups or ones sealed with
`versiones_previas_capturadas=NO`. `verify_audit_chains` exits **2** when there is nothing to
verify (empty filter or no tenants): `--permitir-objetivo-vacio` is for a fresh install only
and the scripts do not pass it.

**Fail-closed (B7).** `restore.sh` exits with an error —leaving the services stopped— if `pg_restore`
fails for any reason: only the harmless inherited-PK error on the `audit_log` partitions ("cannot drop
inherited constraint") is tolerated, and **only if** the exit code is exactly 1, there is at
least one such PK and the final line `errors ignored on restore: N` declares N equal to that count
(any other code, or a larger N —errors without their own line—, is a failure); a non-zero exit
*without* any `pg_restore: error:` line (container down, OOM) is not taken as harmless. **Before
stopping services and before `pg_restore`** (pre-flight) it creates or checks each protected bucket
with `get-object-lock-configuration` and requires `Enabled`; if not (the bucket existed without it and
that cannot be enabled afterwards), it **aborts with nothing stopped or restored**: "no data has been
touched" is true. `backup.sh` no longer swallows a failure while listing
buckets: it requires the mirror to contain `orpycamcp-<slug>-documents` for every entity registered in `public.tenants`;
with the object store unresponsive or the wrong network the backup **fails** instead of finishing
"successfully" with an empty mirror. Both scripts take the network of the `minio` service and use
`amazon/aws-cli` by tag and digest (`S3_CLI_IMAGEN` overrides it).

Two things that are not obvious:

- Objects under **Object-Lock in COMPLIANCE mode cannot be deleted** even if the
  backup does not contain them, not even by root, until their retention expires.
  That is correct: WORM immutability outranks this restore.
- After restoring, check **a radicado with an attachment: open it and download it**.
  That is the only check that exercises the database and the object store at once;
  with only one of the two, a half-restore looks complete.

### Restore drill

A backup whose restore has never been tested is not a backup: it is a file. The
full cycle —dump, destroy, restore, verify the chain— has been exercised against
real containers, and the verification itself was checked **by mutation**: tampering
with one entry with the append-only trigger disabled makes the chain report as
broken and the script exit non-zero.

Repeat it on your own installation at least once, and after every major upgrade.

---

## Upgrades and rollback

### Releases (ADR-053 §3)

A version is a **git tag `X.Y.Z`** (the first one, `1.0.0`). Pushing the tag runs
`.github/workflows/release.yml`:

1. **Full CI** on the tag (`ci.yml`).
2. **Images.** It builds every image in `scripts/release.py plan` (the same list as
   `infra/docker-compose.build.yml`) with `ORPYCA_VERSION=<version>` baked in, scans
   it with Trivy (a CRITICAL with a fix available stops everything **before** the
   push) and publishes it as `ghcr.io/<owner>/docuboto/<service>:<version>`.
3. **`release-<version>.yml`.** A copy of `infra/docker-compose.prod.yml` with every
   image **pinned by digest** (`…:<version>@sha256:…`), attached to the GitHub
   Release. Nothing is deployed by a loose SHA or a mutable tag.
4. **Deploy**, one job per installation (see below).

Without Actions minutes, `./scripts/ci-local.sh all` runs the same jobs as
`ci.yml` locally (uv, Docker and Node 22) and writes one PASS/FAIL per job to
`$TMPDIR/docuboto-ci-local/results.txt`. It mirrors `ci.yml`; it does not replace it.

The production manifest **does not build**: every first-party service starts from
`image: ${ORPYCA_REGISTRY}/<service>:${ORPYCA_VERSION}`, both mandatory (`:?`). A guard
(`shared/tests/test_prod_inmutable.py`) prevents reintroducing `build:` or `latest`;
another (`shared/tests/test_release.py`) requires the release manifest to pin every
image by digest.

**What runs in this municipality** is a query, not a guess:

```bash
curl -s https://<domain>/health            # {"status":"ok","service":"docuboto","version":"1.0.0"}
docker compose -f infra/docker-compose.prod.yml exec postgres \
  psql -U "$DB_USER" -d "$DB_NAME" -c \
  "SELECT version, applied_at FROM public.schema_version ORDER BY applied_at DESC LIMIT 1"
```

The migrator records its version in `public.schema_version` every time it runs.

GHCR images are private when first published: make them public (the code is AGPL)
or, on the server, `docker login ghcr.io` with a `read:packages`-only token.

Without a registry, build on the server itself from the tag with the
`infra/docker-compose.build.yml` override:

```bash
git checkout 1.0.0
export ORPYCA_REGISTRY=local ORPYCA_VERSION=1.0.0
docker compose -f infra/docker-compose.prod.yml -f infra/docker-compose.build.yml \
  build --build-arg ORPYCA_VERSION=1.0.0
```

### Migrations on startup

Every service with a database waits for `migrate-docuboto`: a one-shot container that
runs `migrations/runner.py` with the owner connection on the `app` image. The service only
starts if the migrator finished successfully (`service_completed_successfully`). If a
migration fails, the new version doesn't serve against a schema that isn't its own.

**One chain (ADR-053).** The migrations live in `services/docuboto/migrations/`: `global/`
goes to `public` and `tenant/` goes to every `tenant_*` schema. Each file is named after
what it creates (`210_radicados.sql`, `510_expedientes.sql`), runs in its own transaction,
and is recorded in `public.schema_migrations` under the key `(schema_name, filename)`. The
chains each service had before ADR-053 are archived, not executable, in
`docs/es/historia/esquema/`.

**The lock (ADR-041).** The migrator and `scripts/init_tenant.py` are serialized by **one
global lock** (`orpycamcp_common.migrations.cerrojo`, `pg_try_advisory_lock` on a fixed constant, visible in
`pg_locks`). Whoever doesn't get it retries every half second up to `MIGRATIONS_LOCK_TIMEOUT_S` (600 s by
default). When the deadline passes, it exits with a readable error ("another migration has held the lock for N s
(pid …)"), `service_completed_successfully` isn't met, and **the service doesn't start**. Repeating `up -d` or
scaling replicas is safe. To watch them run by hand **before** starting:
`docker compose -f infra/release-<version>.yml run --rm --no-deps migrate-docuboto`.

**Development.** `docker-compose.yml` does the same: `docker compose up -d --build` builds the images, runs
`migrate-docuboto`, and only then starts each service. The migrator reuses the `orpycamcp/app:dev` image
(`pull_policy: never`, no `build:` of its own). To recreate the app with a new image (the `--no-deps`
form skips the migrator on purpose):

```bash
docker compose build app \
  && docker compose run --rm --no-deps migrate-docuboto \
  && DB_APP_USER=orpycamcp DB_APP_PASSWORD=orpycamcp docker compose up -d --no-build --no-deps app
```

If the migrator fails, the `&&` doesn't recreate the app. Recreating with the **same** image
(`up -d --no-build --no-deps app`) needs no migration: the migrator already applied it.

### Deploy

The `deploy` job of `release.yml` runs once per installation: each municipality is a
GitHub *environment* (with its own secrets and, optionally, a manual approval)
listed in the repository variable `DEPLOY_TARGETS` (e.g. `["silvania"]`). Over SSH,
with the server's host key pinned in `DEPLOY_KNOWN_HOSTS`, it:

1. moves the server's clone (`DEPLOY_PATH`) to the tag;
2. copies `release-<version>.yml` into `infra/`;
3. runs `infra/deploy.sh <version>`.

`infra/deploy.sh` does, in this order:

1. **Backup, blocking.** `infra/backup.sh`; any exit other than 0 (including 3 to 6:
   unanchored chain, incomplete retention, missing or stale secrets bundle) **stops
   the deploy** and leaves the previous version running.
2. `docker compose -f infra/release-<version>.yml pull`.
3. `up -d`: `migrate-docuboto` runs first and the services wait for it to succeed.
4. Waits until `app`'s `/health` answers with **that** version, and records it in
   `infra/.deployed-version`.

The SSH user needs Docker and write access to `DEPLOY_PATH`, nothing else; the key
is used for this alone. By hand, on the server, it is the same:

```bash
cd /srv/orpyca && git fetch --tags && git checkout 1.0.0
gh release download 1.0.0 -p 'release-1.0.0.yml' -D infra/   # or copy it from the Release
./infra/deploy.sh 1.0.0
```

Then check: a real user logging in, a radicado with an attachment (open it and
**download** it) and `GET /api/v1/audit/verify` for one tenant.

### Rollback criteria

Roll back if login fails, an attachment will not download, or the audit chain does
not verify. Do not wait to "see if it settles": the cost of going back grows with
every radicado that comes in. Rolling back is deploying the previous version (its
`release-<version>.yml` is still in `infra/`):

```bash
cat infra/.deployed-version        # the one running now
git checkout 1.0.0 && ./infra/deploy.sh 1.0.0
```

### Expand, deploy, contract (ADR-053 §4)

Migrations have no `down`, and that is deliberate: an automatic downgrade over
archival data is more dangerous than the failure it tries to repair. Rolling back
the code does roll back the installation, **because one release's schema still
serves the previous release's code**. That is not hoped for: it is enforced.

| Step | Rule |
|---|---|
| Expand | The new schema works with the previous code: new tables, new columns that are nullable or have a `DEFAULT`, indexes built `CONCURRENTLY`. |
| Deploy | The new code writes to the new shape and still reads the old one when the new is missing. |
| Contract | Only in the **next release**, once no code reads the old shape: drop columns and tables, in a new file. |

A release is a git tag `X.Y.Z` (the first will be `1.0.0`). The guard
`shared/tests/test_expand_contract.py` takes the chain files the latest tag does
not have as the release in progress, and fails if any of them:

- runs `DROP TABLE` or `DROP COLUMN` on something created in the same release;
- runs `ALTER COLUMN … SET NOT NULL` on a column created in the same release;
- adds a `NOT NULL` column without `DEFAULT` to a table that already existed.

Also, **a file a tag already shipped is never edited**: the runner recorded it by
name and will not apply it again. The change goes in a new file. What the guard
cannot see (whether the previous release's code still reads the column this one
drops) is a review item: contract only what the previous release's code stopped
reading.

With the rule kept, rolling back is the block above: point at the previous version.
**The step-1 backup is not the rollback mechanism**; it is disaster recovery (a lost
server, corrupted data), and it stays mandatory for that reason.

---

## Monitoring and logs

### Centralizing logs with the ELK Stack

```yaml
# docker-compose.prod.yml — add the ELK services
elasticsearch:
  image: docker.elastic.co/elasticsearch/elasticsearch:8.0.0
  environment:
    - discovery.type=single-node
  volumes:
    - elasticsearch-data:/usr/share/elasticsearch/data

logstash:
  image: docker.elastic.co/logstash/logstash:8.0.0
  volumes:
    - ./logstash.conf:/usr/share/logstash/pipeline/logstash.conf:ro

kibana:
  image: docker.elastic.co/kibana/kibana:8.0.0
  ports:
    - "5601:5601"  # Access through Nginx
```

### Metrics with Prometheus

The app exposes **one** `GET /metrics` (text format 0.0.4) on its internal port, `app:8000`
(ADR-047: there used to be five, one per service). The exporter is
in-house (`orpycamcp_common.metricas`, ADR-041): no `prometheus_client`, **no tenant or identity label**, and lazy
measurement (cache of `METRICS_TTL_SECONDS`, 30 s by default, 10 s deadline).

| Family | Signal | Meaning |
|---|---|---|
| `orpycamcp_signal_measured{signal}` | all | `1` = the last measurement was complete in every tenant; `0` = **not verified** |
| `orpycamcp_signal_last_success_timestamp_seconds{signal}` | all | epoch of the last complete measurement |
| `orpycamcp_dlq_pending_events` | `dlq` | pending events in the workflow, signature and notification dead-letter groups |
| `orpycamcp_dlq_oldest_pending_timestamp_seconds` | same | oldest pending event (absent if none) |
| `orpycamcp_worm_exhausted_artifacts{artifact,phase}` | `worm` | WORM artifacts whose retry was exhausted |
| `orpycamcp_audit_chain_breaks`, `_declared_breaks`, `_verification_ok`, `_last_verification_timestamp_seconds` | `audit_chain` | last **complete** `verify_audit_chains` run |
| `orpycamcp_intelligence_sends`, `_barrier_denials`, `_fallbacks`, `_suspensions` | `intelligence` | sends and denials of the intelligence gateway |

**"Not verified" is not 0.** If a signal's measurement fails in any tenant, `orpycamcp_signal_measured{signal="…"} 0`
is emitted and **no value series** of that signal is; never a partial sum. An `orpycamcp_dlq_pending_events 0` with
`signal_measured 1` is a measured zero. With replicas, each reports the same database state: aggregate with `max()`,
not `sum()`.

**How it is protected.** The app answers **404** to any `/metrics` request carrying `X-Forwarded-For`, `Forwarded`
or `X-Real-IP` (Prometheus does not send them; nginx does), and the production nginx answers 404 to `/metrics` on the
API host. In production the app publishes no `ports:`: Prometheus must join `orpycamcp-net` and scrape `app:8000`. **Prometheus is not part of this
version's manifest**; if you deploy it, pin its image by tag **and digest** (`docker buildx imagetools inspect
<image>:<tag>`; never `:latest`) and publish no `ports:` for Prometheus or Grafana: reach them through a tunnel or an
authenticated internal proxy, not by opening the port to the outside.

```yaml
# prometheus.yml (fragment)
scrape_configs:
  - job_name: orpycamcp
    metrics_path: /metrics
    scrape_interval: 60s
    static_configs:
      - targets:
          - app:8000
```

**The audit chain is measured by the last job, not by the scrape.** `orpycamcp_audit_chain_*` comes from
`public.audit_chain_runs`, where `python -m auth.ops.verify_audit_chains` records one row per run (`backup.sh` invokes
it with `--origen backup` and `restore.sh` with `--origen restore`). A run narrowed with `--slug` or
`--excluir-prefijo` is recorded as **partial** and does not count. To have a signal even without a backup, schedule a
daily cron from the host (without mounting the repo writable):

```bash
# /etc/cron.d/orpyca-verify-audit
30 3 * * * orpyca cd /srv/orpyca && docker compose -f infra/docker-compose.prod.yml run --rm --no-deps -T \
  app python -m auth.ops.verify_audit_chains --anclas - --origen cron < /srv/orpyca/backups/anclas-auditoria.tsv
```

(No `--emitir-ancla`: the cron only verifies and records; anchoring stays with `backup.sh`.)

Example alert rules (Prometheus):

```yaml
groups:
  - name: orpycamcp
    rules:
      - alert: SignalNotVerified
        expr: orpycamcp_signal_measured == 0
        for: 15m
      - alert: DeadLetterHasEvents
        expr: max(orpycamcp_dlq_pending_events) by (job, instance) > 0
        for: 1h
      - alert: WormExhausted
        expr: sum(orpycamcp_worm_exhausted_artifacts) > 0
      - alert: AuditChainBroken
        expr: orpycamcp_audit_chain_breaks > 0 or orpycamcp_audit_chain_verification_ok == 0
      - alert: AuditVerificationStale
        expr: time() - orpycamcp_audit_chain_last_verification_timestamp_seconds > 26 * 3600
```

In **development** `audit_chain` will show as not verified or with breaks: the `itest-*` entries left by the
integration tests cannot be deleted (`audit_log` is append-only) and excluding them with `--excluir-prefijo` is, by
construction, a partial run.

### Alerts

Configure alerts for:

- CPU/memory > 80%
- Disk space < 20%
- PostgreSQL replication lag
- SMTP delivery failures
- HTTP error rate > 5%

---

## Security

### Pre-deployment security checklist

> What can be verified in the repository **already has a guard** that runs on every push
> (`shared/tests/test_manifiesto_prod_seguro.py`): the production manifest publishes no
> `ports:` except `edge` (80 and 443), Redis carries `--requirepass ${REDIS_PASSWORD:?}`, every
> credential is mandatory (`${VAR:?}`) and has no default, and third-party images carry **a tag and a
> digest** (`@sha256:`; a tag can be moved in the registry, a digest cannot). `TRUSTED_PROXIES` excludes
> the network's gateway, the nginx template covers the three domains and `infra/.env.example` holds every
> manifest variable. To update an image: `docker buildx imagetools inspect <image>:<tag>`, copy the
> `Digest` into the manifest (and into `docker-compose.yml`, so what is validated in development is what
> reaches production) and run the guard. **Keycloak 24.0.5 is out of support**: upgrading it is a separate
> batch (it changes the admin API and the theme). What depends on the **host** is checked, from outside the server, with
> `scripts/preflight-prod.sh <public-host>` (valid TLS, HTTP redirects to HTTPS, internal
> ports closed). The rest remains manual.

- [ ] All services have TLS enabled (`scripts/preflight-prod.sh`)
- [ ] PostgreSQL is not externally accessible (manifest: guard; host: `preflight-prod.sh`)
- [ ] The object store requires authentication (no public access)
- [ ] Redis requires a password (manifest: guard)
- [ ] Keycloak uses HTTPS with a valid certificate (all three domains have one and `edge` serves them; the `/admin/` console is not public)
- [ ] Only `edge` publishes ports (80 and 443): Docker bypasses ufw for published ports (manifest: guard; host: `scripts/preflight-prod.sh`)
- [ ] `TRUSTED_PROXIES` without the network's gateway (`172.20.128.0/17`, not the `/16`)
- [ ] CORS configured restrictively
- [ ] Audit logging enabled in PostgreSQL and Keycloak
- [ ] Daily backup with recovery testing
- [ ] Per-IP rate limiting in `edge` (`limit_req`) and `RATE_LIMIT_PER_USER` in the app
- [ ] Security headers (HSTS, X-Content-Type-Options, etc.)

### Security headers in Nginx

They live in `infra/nginx/orpyca.conf.template` (the `edge` service). The important part of that template is not the
headers: it is the three things that depend on `X-Forwarded-*` and fail silently
without them — the PKCE login `redirect_uri`, the real client IP sealed into
`audit_log` (an evidentiary record), and per-IP rate limiting. It also sets
`proxy_buffering off`, without which nginx re-buffers to disk every download the
system streams in chunks (attachments, expediente ZIP, AIP).

**HSTS belongs in the proxy, not in the application.** It is a promise about the
whole domain, and the party that can make it is the one terminating TLS; that is
exactly why the SSR frontend does not emit it — emitted by a deployment served
over `http`, it would make the domain unreachable for a year. Every other header
(CSP, `nosniff`, `Referrer-Policy`, `X-Frame-Options`, `Permissions-Policy`) **is
emitted by the SSR itself**: do not duplicate them here, because `add_header` does
not replace the backend's, and two different values of the same header behave
differently per browser.

### Security audit

`docker scan` has been **removed** from the Docker CLI. The pipeline scans every
first-party image with [Trivy](https://trivy.dev) in the `images` job of `release.yml`, **before**
publishing it: it fails on a `CRITICAL` vulnerability that has a fix available
(`--ignore-unfixed`), so a vulnerable image never reaches the registry. By hand, with the
version (never `latest`):

```bash
trivy image --severity CRITICAL --ignore-unfixed "$ORPYCA_REGISTRY/app:$ORPYCA_VERSION"
trivy image --severity CRITICAL --ignore-unfixed postgres:15-bookworm
```

Dependencies are audited separately (`dependency-audit`: `pip-audit` over every
`requirements*.lock`, fail-closed, on the weekly schedule and on every merge request;
`npm-audit`: `npm audit --omit=dev --audit-level=high` over the frontend, on every
pipeline). Neither is skipped silently: a missing or corrupt report is a failure, and so
is a vulnerability with a fix available.

---

## Troubleshooting

### Services won't start

```bash
# 1. Check the logs
docker compose logs postgres
docker compose logs keycloak

# 2. Check the environment variables
docker compose config | grep -A 5 "environment"

# 3. Check network connectivity
docker network ls
docker network inspect orpycamcp-net
```

### PostgreSQL refuses connections

```bash
# Verify authentication
docker compose exec postgres psql -U orpycamcp -d orpycamcp_db -c "SELECT version();"

# Reset the password if needed
docker compose exec postgres psql -U postgres -c \
  "ALTER USER orpycamcp PASSWORD 'new-password';"
```

### The object store won't sync with buckets

The engine is **RustFS** since ADR-027 and ships no `mc` client, so these checks run
with `aws-cli` from a throwaway container on the same network.

```bash
alias s3orp='docker run --rm --network orpycamcp-net \
  -e AWS_ACCESS_KEY_ID="$MINIO_ROOT_USER" \
  -e AWS_SECRET_ACCESS_KEY="$MINIO_ROOT_PASSWORD" \
  amazon/aws-cli --endpoint-url http://minio:9000'

# Object-store health
docker compose exec minio curl -fsS http://localhost:9000/health

# List the buckets
s3orp s3 ls

# Recreate a bucket: **with Object Lock**, or it stops being a preservation bucket.
# Locking can ONLY be enabled at creation time.
s3orp s3api create-bucket --bucket orpycamcp-tenant-documents \
  --object-lock-enabled-for-bucket
```

> **Never delete a preservation bucket to "recreate it clean".** Its objects are under
> COMPLIANCE retention: they cannot be deleted or overwritten — and if you manage to,
> the store is not enforcing the guarantee the transfer record asserts.

### High memory usage

```bash
# Lower buffer_pool_size in PostgreSQL
docker compose down
# Edit docker-compose.prod.yml
sed -i 's/-c shared_buffers=256MB/-c shared_buffers=128MB/' docker-compose.prod.yml
docker compose up -d postgres
```

---

## Next steps

1. **Integrate with your corporate PKI** — use internally signed certificates
2. **Implement corporate SSO** — SAML/OIDC federation with Entra ID, Okta, etc.
3. **Configure cloud backup** — AWS S3, GCS, or Azure Blob Storage
4. **Implement disaster recovery** — replication to a second site
5. **Train the team** — how to operationalize OrpycaMCP

---

**Questions?** Contact: aurigadl@gmail.com
