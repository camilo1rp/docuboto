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

For a small to medium institution (< 5000 users):

- **CPU**: 4 cores (x86-64 or ARM64)
- **RAM**: 16 GB
- **Storage**: 500 GB SSD for PostgreSQL + 1-5 TB for the object store (depending on document volume)
- **Bandwidth**: 100 Mbps

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

- `api-gateway` and `frontend` run with `deploy.replicas: 2`: a fixed host port would be taken by the
  first replica and the second would die with "port is already allocated". nginx resolves the service
  name on every request (`resolver 127.0.0.11`) and spreads load across replicas.
- With nginx on the host, the gateway would see the docker-proxy IP as its peer (see `TRUSTED_PROXIES`,
  below).
- Services call `https://${KEYCLOAK_HOSTNAME}` from inside the network: `edge` carries that name (and
  the other two) as **aliases** on `orpycamcp-net`, so Docker DNS resolves it to `edge` itself, with a
  valid certificate, no hairpin NAT and no external DNS. That is what makes the SSR's PKCE login work
  (`INTERNAL_KEYCLOAK_URL`).

What each domain publishes:

| Domain | Target | Note |
|---|---|---|
| `ORPYCA_REGISTRY` / `ORPYCA_TAG` | Image registry and the immutable tag to deploy (the commit SHA; never `latest`). **Mandatory** (`:?`): without them no `docker compose` command starts | `registry.example.org/orpyca` / `<sha>` |
| `FRONTEND_HOSTNAME` | `frontend:3000` | HSTS; the SSR emits the other headers |
| `API_PUBLIC_HOSTNAME` | `api-gateway:8080` | `/health/ready` only from the `edge` container itself (`127.0.0.1`); probe it with `docker exec <edge> wget --no-check-certificate -qO- --header 'Host: <API_PUBLIC_HOSTNAME>' https://127.0.0.1/health/ready`. The network gateway's IP (`172.20.0.1`) is **not** allowed: it is what nginx sees for any internet client |
| `KEYCLOAK_HOSTNAME` | `keycloak:8080` | **allow-list**: only `/realms/orpycamcp/` and `/resources/`; everything else (including `/admin/`, `/realms/master`, `/realms/master;x/…`) answers 404, and so does any path containing `;` |

Keycloak's admin console is **not published**: use it through an SSH tunnel
(`ssh -L 8080:localhost:8080 server`, towards the container's port) or `docker compose exec keycloak`.

```bash
cd infra
docker compose -f docker-compose.prod.yml --env-file .env config -q   # validate the manifest
docker compose -f docker-compose.prod.yml --env-file .env up -d
docker compose -f docker-compose.prod.yml --env-file .env exec edge nginx -t
```

### Verified IP on the public window (`TRUSTED_PROXIES`)

The window's authorization record (Law 1581/2012, ADR-031) stores the client IP **only if the gateway can vouch for it**. The gateway derives it by walking `X-Forwarded-For` **from the right**, dropping the `TRUSTED_PROXIES` hops; it signs it with `IDENTITY_SIGNING_SECRET` (bound to the route, valid 120 s) and document-service accepts only that signature: it **never** reads `X-Forwarded-For`. For `ip_verificada` to be `true` in production:

1. `IDENTITY_SIGNING_SECRET` set (mandatory in production; without it the IP is null).
2. The api-gateway's `TRUSTED_PROXIES` must include **the `edge` network and the SSR frontend's** (the two hops that chain `X-Forwarded-For`: nginx uses `proxy_add_x_forwarded_for` and the SSR forwards the browser's IP). The manifest default is **`172.20.128.0/17`**: the `ip_range` of `orpycamcp-net` (the subnet is `172.20.0.0/16`, but containers only get addresses from its upper half). If one is missing, the gateway sees the proxy itself as the client: the record shows the proxy's IP, not the person's.

   **Why the network's gateway (`172.20.0.1`) is left out.** nginx *appends* `$remote_addr` to whatever the client sent in `X-Forwarded-For`. If `$remote_addr` ever were `172.20.0.1` (an IPv6 client served through docker-proxy, or a process on the server itself) and that IP were trusted, the API gateway would skip it and keep the entry **forged by the client**, which would end up sealed in `audit_log` and in the record (ADR-031). With the network's gateway outside the trusted range the outcome is *fail-closed*: `172.20.0.1` is recorded, never an invented IP. A guard (`test_trusted_proxies_excluye_el_gateway_de_la_red`) prevents going back to a `/16`. If you change the network's subnet in the compose file, change `TRUSTED_PROXIES` with it.

   **IPv6.** Docker delivers IPv6 clients through its docker-proxy, which hides the real IP (you would see `172.20.0.1`). To keep it, enable `"ip6tables": true` in `/etc/docker/daemon.json`; without it an IPv6 client's IP shows as `172.20.0.1` (uninformative, but it cannot be forged).
3. With `TRUSTED_PROXIES` empty (the default) the header is ignored and the socket address is used: safe, but uninformative.

   **The API gateway refuses to start if it is left that way (ARC-B4).** The production manifest sets `APP_ENV=production` and `GATEWAY_RED_DOCKER_GATEWAY=172.20.0.1` (it must equal the network's `gateway:`; a guard checks it); if `TRUSTED_PROXIES` covers that IP, startup ends with an error that explains it. If the variable is empty, the IP is derived from the container's default route. **Production only:** in development `docker-compose.yml` sets `172.16.0.0/12` (which does contain it) on purpose and is not checked.

**Important:** the IP is only reliable if the frontend is reachable **only through `edge`**. An SSR port exposed directly (in development, `19300` with `XFF_DEPTH=1`) accepts a client-forged `X-Forwarded-For` and chains it as if trusted; in production no service except `edge` publishes ports (manifest guard).

If your `infra/.env` comes from an earlier version and sets `TRUSTED_PROXIES=172.20.0.0/16`, **change it** to `172.20.128.0/17` (or delete it): with the `/16` the network's gateway becomes trusted again.

Without a valid signature the record says `ip=null, ip_verificada=false` and filing is **not** blocked.

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
| `DB_USER` | PostgreSQL user | `orpycamcp_prod` |
| `DB_PASSWORD` | PostgreSQL password (32+ characters) | `<openssl rand -base64 32>` |
| `REDIS_PASSWORD` | Redis password (32+ characters) | `<openssl rand -base64 32>` |
| `MINIO_ROOT_PASSWORD` | Object-store root password (the `MINIO_` prefix is historical, ADR-027) | `<openssl rand -base64 32>` |
| `KEYCLOAK_HOSTNAME` | Keycloak FQDN domain | `keycloak.yourdomain.com` |
| `KEYCLOAK_ADMIN_PASSWORD` | Keycloak admin password | `<secure password>` |
| `KEYCLOAK_*_SECRET` | OAuth2 client secrets | `<32 char hex>` |
| `FRONTEND_HOSTNAME` | Public FQDN of the SSR frontend (defines `ORIGIN` and the login `redirect_uri`) | `app.yourdomain.com` |
| `API_PUBLIC_HOSTNAME` | Public FQDN of the api-gateway as the browser sees it | `api.yourdomain.com` |
| `SIGNATURE_SECRET` | HMAC key for signature sealing (E06). Random, rotate it. **The service refuses to start without it** | `<openssl rand -base64 32>` |
| `MINIO_ROOT_USER` | Object store user. **storage-service refuses to start without it** | `orpyca_prod` |
| `KNOWLEDGE_INTERNAL_TOKEN` | Internal ingestion token (E21). Empty ⇒ ingestion is fail-closed | `<openssl rand -base64 32>` |
| `SMTP_HOST` | SMTP host | `smtp.gmail.com` |
| `SMTP_PASSWORD` | SMTP password or application token | `app-specific-password` |

### Variables that block startup when missing

Three secrets used to ship with a **working default** published in an AGPL
repository: `SIGNATURE_SECRET` was `change-me-in-production`, and the object-store
credentials were the development ones printed in `CLAUDE.md` itself. A deployment
that forgot to set them **did not fail** — it started up and signed (or stored
official attachments) with a key anyone can read on GitHub.

The defaults are now empty strings **and** startup checks them: `signature-service`
and `storage-service` refuse to serve without `SIGNATURE_SECRET`,
`MINIO_ACCESS_KEY` and `MINIO_SECRET_KEY`. Example placeholders
(`change-me-in-production` and friends) are rejected too, in case someone copies
`.env.example` untouched. The error message names the variable, never the value
received.

If you use email ingestion (E19), `EMAIL_ENCRYPTION_KEY` is required: mailbox
credentials are encrypted at rest with it. There are no `IMAP_*` variables any
more — each mailbox is configured from `/admin/correo` and lives in
`email_cuenta`. For automatic polling, `EMAIL_SONDEO_TENANT_SLUG` declares
**which institution owns the mailboxes the process polls**; without it the worker
does not start even when `EMAIL_SONDEO_ENABLED` is on.

### CORS

`CORS_ALLOW_ORIGINS` is **empty by default and the middleware is not installed at
all**. This is not a regression: the frontend talks to the gateway **from the
server** (SSR), not from the browser, so a typical deployment needs no CORS. Only
declare it for a third-party integration or an SPA on another domain:

```bash
CORS_ALLOW_ORIGINS=https://sgd.entidad.gov.co,https://portal.entidad.gov.co
```

> The `*` wildcard is accepted but **disables credentials**. The previous setting
> was `allow_origins=["*"]` **together with** `allow_credentials=True`, hardcoded:
> with credentials Starlette cannot answer `*`, so it reflects whatever `Origin`
> arrives — any page on the internet could call the gateway with the visitor's
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
    -v "$PWD/scripts:/scripts:ro" auth-service \
    python /scripts/audit_partitions.py --meses 3

# For monitoring: exit 0 if up to date, 1 if next month's partition is missing.
... python /scripts/audit_partitions.py --verificar
```

It runs several months ahead on purpose: if the cron fails silently, there is
slack before entries start landing in the default partition again.

**A cron failure is visible without reading any log.** auth-service's
`GET /health/ready` applies the same logic as `--verificar` (does next month's partition
exist?) read-only and with the application role: if it is missing it answers **200** with
`"degraded": ["audit_particiones"]` and `"audit_particiones": "degraded"` (an operations
alert, it does not pull the service out of the balancer); if it cannot query, `unknown`
and it does not accuse. Alert on that field in the monitoring you already have.

### Daily audit-chain verification

`verify_chain` only ran on demand (`GET /api/v1/audit/verify`) or on restore. Schedule it
**daily**, with the module that `backup.sh` and `restore.sh` also use:

```bash
# Exit 0 if ALL chains are intact and none went backwards against the last anchor; 1 on a
# break, a regression or an unreadable anchor. Read-only.
docker compose -f infra/docker-compose.prod.yml run --rm --no-deps -T \
    -e "DATABASE_URL=postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@postgres:5432/${DB_NAME}" \
    auth-service python -m app.ops.verify_audit_chains \
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
`app.ops.registrar_restauracion` (`storage.restauracion` audit entry plus
`preservacion_worm_objeto.version_id` update, one transaction). `restore.sh` reuses it instead of the previous heredoc.
In development, `BACKUP_AUDIT_EXCLUIR_PREFIJOS="itest"` leaves out the disposable tenants
the integration tests left behind (the record is append-only and cannot be deleted);
**do not set it in production**.

> History already in `audit_log_default` is **not moved**. Rewriting rows of an
> append-only, hash-chained record means touching the evidentiary basis of the
> whole archive, and tidying up old data does not justify it. The function starts
> at the first non-overlapping month: the default freezes and stops growing.

### Issuer (`iss`) contract — critical

The tokens' `iss` is **always the public URL** through which the browser logs in. Any
service that validates JWTs (`auth-service`, `signature-service`) must expect exactly
that `iss` or it will reject **every** token (fail-closed).

- **Production (single host):** Keycloak runs with `--hostname-strict=true
  --hostname=${KEYCLOAK_HOSTNAME}`, so the browser and the services use the same host.
  `expected_issuer` is derived from `KEYCLOAK_URL` and **no override is needed**.
- **Development (`docker-compose.yml`):** there is a split (browser `localhost:19180`
  ↔ internal network `keycloak:8080`); that is why `KEYCLOAK_ISSUER` is set there with
  the realm's public URL.

On startup, `auth-service` and `signature-service` log the expected `iss` and emit a
**fail-fast WARNING** if `KEYCLOAK_URL` points at an internal Docker host without
`KEYCLOAK_ISSUER` defined (a configuration that would reject every token). Check this
log after deployment:

```bash
docker compose -f docker-compose.prod.yml logs auth-service | grep "issuer esperado"
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
docker compose restart document-service tenant-service auth-service
```

---

## Deployment with Docker Compose

### Using docker-compose.prod.yml

```bash
# 1. Clone the repository
git clone https://gitlab.com/orpyca/orfeo_mcp.git
cd orfeo_mcp

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
docker compose logs -f api-gateway

# 6. Create the first tenant (once Keycloak is up)
#    The script runs ON THE HOST, not inside a container, and takes its three
#    arguments positionally: <slug> <name> <code>.
cd ..
COMPOSE_FILE=infra/docker-compose.prod.yml ENV_FILE=infra/.env \
  ./scripts/init-tenant.sh my-institution "My Institution" MYINST
```

### Post-deployment verification

```bash
# Review each service's logs
docker compose logs -f postgres      # Connection errors
docker compose logs -f keycloak      # OIDC/realm issues
docker compose logs -f api-gateway   # Errores de ruteo

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

Every service exposes **two** routes, and they are not interchangeable:

| Route | Question | Checks | Consumed by |
|---|---|---|---|
| `/health` | Is the process answering? | Nothing | The container `healthcheck` |
| `/health/ready` | Can it serve traffic **right now**? | Database, Redis, SMTP, event consumer | The load balancer / reverse proxy |

`/health` **depends on nothing, deliberately**. Restarting a service because
Postgres is down does not fix Postgres: it discards in-flight work and adds a
restart storm to an ongoing incident. `/health/ready` returns **503** when the
service cannot work, which is the signal for the balancer to stop sending it
requests **without restarting it**.

Configure the reverse proxy to drop a service whose `/health/ready` answers 503;
leave restarts to `/health`.

```bash
# Aggregate diagnosis (gateway + its ten services)
curl -s https://yourdomain.gov.co/health/ready | jq
```

In `checks.consumer`, the value `reiniciando` means the event consumer died and
is retrying: the service answers requests, but what that consumer does —routing
incoming mail, sending email, sealing signatures, feeding the search index— **is
not happening**. Worth investigating even while the service shows as available.

Probes report state as a single word (`ok`, `error`, `unreachable`) and never the
failure detail: they are reachable without authentication and the text of a
network exception carries internal hostnames and ports. The real reason is in the
service log.

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

### Option 2: Kubernetes (not supported yet)

**The repository ships no Kubernetes manifests or Helm charts** (no `Chart.yaml`, no
`kustomization`), and automatic conversion from Compose has not been tested: the `migrate-<service>`
containers, the `service_completed_successfully` dependencies and file-based secrets do not translate
by themselves. The supported route is Docker Compose (Option 1) or Swarm. If you scale replicas on any
orchestrator, **review the in-memory state first**: the api-gateway limiter counts in Redis, but the
assistant history, the MCP proposals' `jti` consumption and other caches are per process.

### Service scalability

Stateless services can be scaled horizontally:

```yaml
# In docker-compose.prod.yml
api-gateway:
  deploy:
    replicas: 3  # 3 instancias balanceadas por carga

storage-service:
  deploy:
    replicas: 2  # Para throughput de uploads

document-service:
  deploy:
    replicas: 2  # For radicado searches
```

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

Dumps PostgreSQL (every schema: `public`, holding the tenant registry and the
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
buckets: it requires the mirror to contain `orpycamcp-<slug>-documents` for every tenant that has not been decommissioned (`active` and `suspended`);
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

> **There was no procedure.** No upgrade section, no command to migrate tenants
> that already exist —the only runner is `scripts/init_tenant.py`, designed for
> PROVISIONING and with the source tree mounted—, and no immutable tag to return
> to: the manifest builds from source on the server itself, so "the previous
> version" was not a thing that existed anywhere.

### First things first: the version has to be a thing

The production manifest (`infra/docker-compose.prod.yml`) **does not build**: every
first-party service starts from `image: ${ORPYCA_REGISTRY}/<service>:${ORPYCA_TAG}`, and
both variables are **mandatory** (`:?`; without them no `docker compose` command starts).
The GitLab `build-images` job publishes `$CI_REGISTRY_IMAGE/<service>:$CI_COMMIT_SHA`
(scanned with Trivy first), so `ORPYCA_REGISTRY=$CI_REGISTRY_IMAGE` and `ORPYCA_TAG=<sha>`
name a version that exists and can be returned to. A guard
(`shared/tests/test_prod_inmutable.py`) prevents reintroducing `build:` or a `latest` tag.

Without a registry, build on the server itself with the
`infra/docker-compose.build.yml` override (it tags each image with the same name, so the
SHA stays on the host and rollback still works):

```bash
export ORPYCA_REGISTRY=local ORPYCA_TAG=$(git rev-parse HEAD)
docker compose -f infra/docker-compose.prod.yml -f infra/docker-compose.build.yml build
```

`infra/DOCKER_REGISTRY.md` documents the GitLab registry (`build-images` in
`.gitlab-ci.yml`); there is no GitHub Actions pipeline in this repository.

### Migrations on startup

Every service with a database waits for its `migrate-<service>` (`auth`, `tenant`,
`document`, `storage`, `workflow`, `archive`, `notification`, `signature`, `knowledge`): a
one-shot container that runs `migrations/runner.py` with the owner connection on the
**same immutable image** that is going to serve, and the service only starts if it
finished successfully (`service_completed_successfully`). If a migration fails, the new
version does not serve against a schema that is not its own. The runner is idempotent and
serializes through `pg_advisory_lock`, so repeating `up -d` or scaling replicas is safe.
Step 4 below remains valid to run them by hand **before** starting (e.g. to watch their
output). The development compose does not do this: migrations are applied by hand there.

### Procedure

```bash
# 1. VERIFIED backup. Not "a backup": one that finished with the OK seal.
ENV_FILE=infra/.env BACKUP_DIR=/srv/orpyca/backups ./infra/backup.sh

# 2. Record the current version — this is what you go back to.
docker compose -f infra/docker-compose.prod.yml images | tee /srv/orpyca/previous-version.txt

# 3. Fetch the new version (without starting it yet).
export ORPYCA_REGISTRY=<registry/project>   # e.g. registry.gitlab.com/group/orpycamcp
export ORPYCA_TAG=<commit-sha>
docker compose -f infra/docker-compose.prod.yml pull

# 4. Migrations. Each service applies its own, and the runner walks EVERY
#    tenant_* schema it finds, so a newly provisioned tenant is not left behind.
#    They are idempotent by convention (ADR-012): re-running them is safe.
#    NOTE: it is `python migrations/runner.py`, a script — NOT
#    `python -m app.migrations.runner`, which is a module that does not exist.
for svc in auth tenant document archive storage workflow notification signature knowledge; do
  docker compose -f infra/docker-compose.prod.yml run --rm --no-deps \
    "${svc}-service" python migrations/runner.py
done

# 5. Deploy.
docker compose -f infra/docker-compose.prod.yml up -d

# 6. Verify. The three checks that cover database, object store and identity:
#    - a real user logging in
#    - a radicado with an attachment: open it and DOWNLOAD it
#    - GET /api/v1/audit/verify for one tenant
```

### Rollback criteria

Roll back if, after step 6, login fails, an attachment will not download, or the
audit chain does not verify. Do not wait to "see if it settles": the cost of going
back grows with every radicado that comes in.

```bash
export ORPYCA_REGISTRY=<registry/project>
export ORPYCA_TAG=<previous-sha>
docker compose -f infra/docker-compose.prod.yml up -d
```

**Rolling back the CODE does not roll back the SCHEMA.** Migrations have no `down`,
and that is deliberate: an automatic downgrade over archival data is more dangerous
than the failure it tries to repair. If the new version changed the schema
incompatibly, the real rollback is to **restore the step-1 backup** with
`infra/restore.sh`, accepting the loss of everything since. That is why step 1 is
not optional, and why upgrades belong in a low-activity window.

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

```yaml
prometheus:
  image: prom/prometheus:latest
  volumes:
    - ./infra/prometheus.yml:/etc/prometheus/prometheus.yml:ro
    - prometheus-data:/prometheus

grafana:
  image: grafana/grafana:latest
  ports:
    - "3000:3000"  # Through Nginx
```

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
- [ ] Rate limiting enabled on api-gateway
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

### Voice dictation (whisper) outside the production manifest

`whisper-service` (profile `assistant`, starts off) is **not in `infra/docker-compose.prod.yml` in v1, on
purpose**: `mcp-server` does not set `STT_PROVIDER`, so the default `disabled` applies and dictation is
off; no piece is missing. Declaring it under `profiles: [assistant]` would cost an interpolation side
effect: Compose interpolates the whole file, inactive profiles included, forcing every operator to
define `WHISPER_INTERNAL_TOKEN` even without dictation. When an entity asks for it, add the service with
its token **and** `STT_PROVIDER` on `mcp-server`; a guard (`test_stt_en_prod_tiene_quien_lo_sirva`)
fails if dictation is switched on without the service.

### Security audit

`docker scan` has been **removed** from the Docker CLI. The pipeline scans every
first-party image with [Trivy](https://trivy.dev) in the `build-images` job, **before**
publishing it: it fails on a `CRITICAL` vulnerability that has a fix available
(`--ignore-unfixed`), so a vulnerable image never reaches the registry. By hand, with the
immutable SHA tag (never `latest`):

```bash
trivy image --severity CRITICAL --ignore-unfixed "$ORPYCA_REGISTRY/api-gateway:$ORPYCA_TAG"
trivy image --severity CRITICAL --ignore-unfixed pgvector/pgvector:pg15
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
