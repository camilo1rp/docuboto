# Production Deployment Guide

> **IMPORTANT**: This guide is a starting point. Consult with your institution's DevOps and security specialists before deploying to production.

## Table of contents

- [Prerequisites](#requisitos-previos)
- [Infrastructure configuration](#configuracion-de-infraestructura)
- [Secrets and environment variables](#secretos-y-variables-de-entorno)
- [Deployment with Docker Compose](#despliegue-con-docker-compose)
- [Clustering and scalability](#clustering-y-escalabilidad)
- [Backups and recovery](#respaldos-y-recuperacion)
- [Monitoring and logs](#monitoreo-y-logs)
- [Security](#seguridad)
- [Troubleshooting](#troubleshooting)

---

## Prerequisites

### Minimum hardware

For a small to medium institution (< 5000 users):

- **CPU**: 4 cores (x86-64 or ARM64)
- **RAM**: 16 GB
- **Storage**: 500 GB SSD for PostgreSQL + 1-5 TB for MinIO (depending on document volume)
- **Bandwidth**: 100 Mbps

### Software

- Docker 24.0+
- Docker Compose 2.20+
- Linux (RHEL, Ubuntu, Debian) or Kubernetes
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
# Actualizar sistema operativo
sudo apt update && sudo apt upgrade -y

# Instalar Docker
curl -fsSL https://get.docker.com | sh
sudo usermod -aG docker $USER

# Instalar Docker Compose
sudo curl -L "https://github.com/docker/compose/releases/latest/download/docker-compose-$(uname -s)-$(uname -m)" -o /usr/local/bin/docker-compose
sudo chmod +x /usr/local/bin/docker-compose

# Crear directorio de datos (fuera del árbol del código)
sudo mkdir -p /srv/orpyca/{postgres,minio,redis,certs}
sudo chown $USER:$USER /srv/orpyca -R
```

### 2. Obtain a TLS certificate

With Let's Encrypt (free, requires port 80 to be accessible):

```bash
sudo apt install certbot
sudo certbot certonly --standalone -d keycloak.yourdomain.com
# Certificados en /etc/letsencrypt/live/keycloak.yourdomain.com/

# Copiar a la carpeta del proyecto (renovación automática)
sudo cp /etc/letsencrypt/live/keycloak.yourdomain.com/fullchain.pem /srv/orpyca/certs/tls.crt
sudo cp /etc/letsencrypt/live/keycloak.yourdomain.com/privkey.pem /srv/orpyca/certs/tls.key
sudo chown $USER:$USER /srv/orpyca/certs/tls.*
```

With a corporate CA: provide `tls.crt` and `tls.key` in the certs folder.

### 3. Configure the firewall

```bash
# Asumir UFW en Ubuntu
sudo ufw default deny incoming
sudo ufw default allow outgoing

# Permitir SSH (CRÍTICO: no bloquee tu acceso)
sudo ufw allow ssh

# Permitir Nginx (proxy reverso)
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

# Denegar acceso directo a servicios internos
sudo ufw deny 5432  # PostgreSQL
sudo ufw deny 6379  # Redis
sudo ufw deny 8080  # API Gateway sin proxy
sudo ufw deny 9000  # MinIO

sudo ufw enable
```

### 4. Install Nginx as a reverse proxy (recommended)

```bash
sudo apt install nginx

# Ver plantilla en infra/nginx.conf.example
sudo cp infra/nginx.conf.example /etc/nginx/sites-available/orpyca
sudo ln -s /etc/nginx/sites-available/orpyca /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

---

## Secrets and environment variables

### CRITICAL! Secure handling of secrets

**NEVER** commit `.env` to Git. Each environment must have different secrets.

```bash
# 1. Crear archivo .env en el servidor (local, no trackeado)
cd /srv/orpyca
cp infra/.env.example .env

# 2. Generar contraseñas fuertes
openssl rand -base64 32  # PostgreSQL password
openssl rand -base64 32  # Redis password
openssl rand -base64 32  # MinIO password
openssl rand -base64 32  # Keycloak admin password

# 3. Editar .env con valores reales
nano .env
```

### Required variables

| Variable | Description | Example |
|---|---|---|
| `DB_USER` | PostgreSQL user | `orpycamcp_prod` |
| `DB_PASSWORD` | PostgreSQL password (32+ characters) | `<openssl rand -base64 32>` |
| `REDIS_PASSWORD` | Redis password (32+ characters) | `<openssl rand -base64 32>` |
| `MINIO_ROOT_PASSWORD` | MinIO password | `<openssl rand -base64 32>` |
| `KEYCLOAK_HOSTNAME` | Keycloak FQDN domain | `keycloak.yourdomain.com` |
| `KEYCLOAK_ADMIN_PASSWORD` | Keycloak admin password | `<secure password>` |
| `KEYCLOAK_*_SECRET` | OAuth2 client secrets | `<32 char hex>` |
| `SMTP_HOST` | SMTP host | `smtp.gmail.com` |
| `SMTP_PASSWORD` | SMTP password or application token | `app-specific-password` |

### Secret rotation

Every 90 days (or per corporate policy):

```bash
# 1. Generar nueva contraseña
NEW_PASSWORD=$(openssl rand -base64 32)

# 2. Actualizar en PostgreSQL
docker compose exec postgres psql -U orpycamcp -c "ALTER USER orpycamcp PASSWORD '$NEW_PASSWORD';"

# 3. Actualizar .env y reiniciar servicios
sed -i "s/DB_PASSWORD=.*/DB_PASSWORD=$NEW_PASSWORD/" .env
docker compose restart document-service tenant-service auth-service
```

---

## Deployment with Docker Compose

### Using docker-compose.prod.yml

```bash
# 1. Clonar repositorio
git clone https://gitlab.com/orpyca/orfeo_mcp.git
cd orfeo_mcp

# 2. Crear estructura de directorios
mkdir -p infra/certs infra/logs

# 3. Configurar variables de entorno
cp infra/.env.example infra/.env
nano infra/.env  # Editar con valores reales

# 4. Levantar stack
cd infra
docker compose -f docker-compose.prod.yml up -d

# 5. Verificar estado
docker compose ps
docker compose logs -f api-gateway

# 6. Crear primer tenant (después de que Keycloak esté listo)
docker compose exec document-service /scripts/init-tenant.sh \
  --name "Mi Institución" \
  --slug "mi-institucion" \
  --code "MIST"
```

### Post-deployment verification

```bash
# Revisar logs de cada servicio
docker compose logs -f postgres      # Errores de conexión
docker compose logs -f keycloak      # OIDC/realm issues
docker compose logs -f api-gateway   # Errores de ruteo

# Probar health check (a través de Nginx si está configurado)
curl -k https://keycloak.yourdomain.com/health

# Verificar que MinIO está funcionando
docker compose exec minio mc ls minio

# Verificar que Redis está funcionando
docker compose exec redis redis-cli -a $REDIS_PASSWORD ping
```

---

## Clustering and scalability

For large institutions (> 10,000 users):

### Option 1: Docker Swarm (simple)

```bash
# Inicializar cluster
docker swarm init
docker swarm join --token <TOKEN> manager-node-ip

# Desplegar stack
docker stack deploy -c docker-compose.prod.yml orpyca-mcp
```

### Option 2: Kubernetes (recommended)

Convert to Helm charts:

```bash
# Convertir docker-compose a Kubernetes
docker-compose -f docker-compose.prod.yml config | docker-to-k8s > orpycamcp-k8s.yaml

# Desplegar en cluster K8s
kubectl apply -f orpycamcp-k8s.yaml
```

### Service scalability

Stateless services can be scaled horizontally:

```yaml
# En docker-compose.prod.yml
api-gateway:
  deploy:
    replicas: 3  # 3 instancias balanceadas por carga

storage-service:
  deploy:
    replicas: 2  # Para throughput de uploads

document-service:
  deploy:
    replicas: 2  # Para búsquedas de radicados
```

---

## Backups and recovery

### Automatic PostgreSQL backup

```bash
#!/bin/bash
# infra/backup.sh

DATE=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="/srv/orpyca/backups"

mkdir -p $BACKUP_DIR

# Respaldar base de datos
docker compose exec -T postgres pg_dump -U orpycamcp orpycamcp_db | \
  gzip > $BACKUP_DIR/orpycamcp_$DATE.sql.gz

# Respaldar datos MinIO
docker compose exec -T minio mc mirror \
  minio/ /backup/minio-$DATE/

# Limpiar respaldos antiguos (> 30 días)
find $BACKUP_DIR -name "*.gz" -mtime +30 -delete

echo "Backup completado: $BACKUP_DIR/orpycamcp_$DATE.sql.gz"
```

Schedule with cron:

```bash
# Ejecutar diariamente a las 2:00 AM
0 2 * * * /srv/orpyca/backup.sh
```

### Recovery from backup

```bash
# 1. Detener servicios
docker compose down

# 2. Restaurar PostgreSQL
gunzip < /srv/orpyca/backups/orpycamcp_20260601_020000.sql.gz | \
  docker compose exec -T postgres psql -U orpycamcp orpycamcp_db

# 3. Restaurar MinIO
docker compose exec -T minio mc mirror \
  /backup/minio-20260601/ minio/

# 4. Reiniciar servicios
docker compose up -d
```

---

## Monitoring and logs

### Centralizing logs with the ELK Stack

```yaml
# docker-compose.prod.yml — agregar servicios ELK
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
    - "5601:5601"  # Acceso a través de Nginx
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
    - "3000:3000"  # A través de Nginx
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

- [ ] All services have TLS enabled
- [ ] PostgreSQL is not externally accessible
- [ ] MinIO requires authentication (no public access)
- [ ] Redis requires a password
- [ ] Keycloak uses HTTPS with a valid certificate
- [ ] Firewall blocks internal ports (5432, 6379, 9000)
- [ ] CORS configured restrictively
- [ ] Audit logging enabled in PostgreSQL and Keycloak
- [ ] Daily backup with recovery testing
- [ ] Rate limiting enabled on api-gateway
- [ ] Security headers (HSTS, X-Content-Type-Options, etc.)

### Security headers in Nginx

```nginx
# infra/nginx.conf
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "SAMEORIGIN" always;
add_header X-XSS-Protection "1; mode=block" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
```

### Security audit

```bash
# Escanear imágenes Docker en busca de vulnerabilidades
docker scan api-gateway:latest
docker scan postgres:15-alpine

# Usar Trivy para análisis de vulnerabilidades
trivy image api-gateway:latest
```

---

## Troubleshooting

### Services won't start

```bash
# 1. Verificar logs
docker compose logs postgres
docker compose logs keycloak

# 2. Comprobar variables de entorno
docker compose config | grep -A 5 "environment"

# 3. Verificar conectividad de red
docker network ls
docker network inspect orpycamcp-net
```

### PostgreSQL refuses connections

```bash
# Verificar autenticación
docker compose exec postgres psql -U orpycamcp -d orpycamcp_db -c "SELECT version();"

# Resetear contraseña si es necesario
docker compose exec postgres psql -U postgres -c \
  "ALTER USER orpycamcp PASSWORD 'new-password';"
```

### MinIO won't sync with buckets

```bash
# Verificar estado de MinIO
docker compose exec minio mc status minio

# Listar buckets
docker compose exec minio mc ls minio/

# Recrear bucket si está corrupto
docker compose exec minio mc rb minio/orpycamcp-tenant-documents
docker compose exec minio mc mb minio/orpycamcp-tenant-documents
```

### High memory usage

```bash
# Reducir buffer_pool_size en PostgreSQL
docker compose down
# Editar docker-compose.prod.yml
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
