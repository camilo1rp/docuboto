# Guía de Despliegue en Producción

> **IMPORTANTE**: Esta guía es un punto de partida. Consulte con especialistas en DevOps y seguridad de su institución antes de desplegar en producción.

## Tabla de contenidos

- [Requisitos previos](#requisitos-previos)
- [Configuración de infraestructura](#configuracion-de-infraestructura)
- [Secretos y variables de entorno](#secretos-y-variables-de-entorno)
- [Despliegue con Docker Compose](#despliegue-con-docker-compose)
- [Clustering y escalabilidad](#clustering-y-escalabilidad)
- [Respaldos y recuperación](#respaldos-y-recuperacion)
- [Monitoreo y logs](#monitoreo-y-logs)
- [Seguridad](#seguridad)
- [Troubleshooting](#troubleshooting)

---

## Requisitos previos

### Hardware mínimo

Para una institución pequeña a mediana (< 5000 usuarios):

- **CPU**: 4 cores (x86-64 o ARM64)
- **RAM**: 16 GB
- **Almacenamiento**: 500 GB SSD para PostgreSQL + 1-5 TB para MinIO (según volumen de documentos)
- **Ancho de banda**: 100 Mbps

### Software

- Docker 24.0+
- Docker Compose 2.20+
- Linux (RHEL, Ubuntu, Debian) o Kubernetes
- Certificado TLS válido (Let's Encrypt gratuito o CA corporativa)
- Dominio DNS resuelto

### Conocimientos

- Administración de Linux/Docker
- Seguridad de redes (firewall, VPN)
- Bases de datos PostgreSQL
- Backup y recovery

---

## Configuración de infraestructura

### 1. Preparar el servidor

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

### 2. Obtener certificado TLS

Con Let's Encrypt (gratuito, requiere puerto 80 accesible):

```bash
sudo apt install certbot
sudo certbot certonly --standalone -d keycloak.yourdomain.com
# Certificados en /etc/letsencrypt/live/keycloak.yourdomain.com/

# Copiar a la carpeta del proyecto (renovación automática)
sudo cp /etc/letsencrypt/live/keycloak.yourdomain.com/fullchain.pem /srv/orpyca/certs/tls.crt
sudo cp /etc/letsencrypt/live/keycloak.yourdomain.com/privkey.pem /srv/orpyca/certs/tls.key
sudo chown $USER:$USER /srv/orpyca/certs/tls.*
```

Con CA corporativa: proporcione `tls.crt` y `tls.key` en la carpeta de certs.

### 3. Configurar firewall

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

### 4. Instalar Nginx como proxy reverso (recomendado)

```bash
sudo apt install nginx

# Ver plantilla en infra/nginx.conf.example
sudo cp infra/nginx.conf.example /etc/nginx/sites-available/orpyca
sudo ln -s /etc/nginx/sites-available/orpyca /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx
```

---

## Secretos y variables de entorno

### ¡CRÍTICO! Manejo seguro de secretos

**NUNCA** commitee `.env` a Git. Cada entorno debe tener secretos diferentes.

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

### Variables requeridas

| Variable | Descripción | Ejemplo |
|---|---|---|
| `DB_USER` | Usuario PostgreSQL | `orpycamcp_prod` |
| `DB_PASSWORD` | Contraseña PostgreSQL (32+ caracteres) | `<openssl rand -base64 32>` |
| `REDIS_PASSWORD` | Contraseña Redis (32+ caracteres) | `<openssl rand -base64 32>` |
| `MINIO_ROOT_PASSWORD` | Contraseña MinIO | `<openssl rand -base64 32>` |
| `KEYCLOAK_HOSTNAME` | Dominio FQDN de Keycloak | `keycloak.yourdomain.com` |
| `KEYCLOAK_ADMIN_PASSWORD` | Contraseña admin Keycloak | `<secure password>` |
| `KEYCLOAK_*_SECRET` | Secrets de clientes OAuth2 | `<32 char hex>` |
| `FRONTEND_HOSTNAME` | FQDN público del frontend SSR (define `ORIGIN` y el `redirect_uri` del login) | `app.yourdomain.com` |
| `API_PUBLIC_HOSTNAME` | FQDN público del api-gateway que ve el navegador | `api.yourdomain.com` |
| `SIGNATURE_SECRET` | Clave HMAC del sellado de firma (E06). Aleatoria, rotar | `<openssl rand -base64 32>` |
| `KNOWLEDGE_INTERNAL_TOKEN` | Token de ingesta interna (E21). Vacío ⇒ ingesta fail-closed | `<openssl rand -base64 32>` |
| `SMTP_HOST` | Host SMTP | `smtp.gmail.com` |
| `SMTP_PASSWORD` | Contraseña o token de aplicación SMTP | `app-specific-password` |

### Contrato de issuer (`iss`) — crítico

El `iss` de los tokens es **siempre la URL pública** por la que el navegador hace
login. Cualquier servicio que valide JWT (`auth-service`, `signature-service`) debe
esperar exactamente ese `iss` o rechazará **todos** los tokens (fail-closed).

- **Producción (single-host):** Keycloak corre con `--hostname-strict=true
  --hostname=${KEYCLOAK_HOSTNAME}`, por lo que navegador y servicios usan el mismo
  host. `expected_issuer` se deriva de `KEYCLOAK_URL` y **no hace falta override**.
- **Desarrollo (`docker-compose.yml`):** hay split (navegador `localhost:19180` ↔
  red interna `keycloak:8080`); por eso allí se fija `KEYCLOAK_ISSUER` con la URL
  pública del realm.

Al arrancar, `auth-service` y `signature-service` registran en el log el `iss`
esperado y emiten un **WARNING fail-fast** si `KEYCLOAK_URL` apunta a un host
interno de Docker sin `KEYCLOAK_ISSUER` definido (configuración que rechazaría todo
token). Verifica este log tras el despliegue:

```bash
docker compose -f docker-compose.prod.yml logs auth-service | grep "issuer esperado"
```

### Frontend SSR

El frontend (SvelteKit + adapter-node) corre en su **propio contenedor** detrás del
proxy reverso. Necesita resolver correctamente el `origin` para construir el
`redirect_uri` del login PKCE:

- `ORIGIN=https://${FRONTEND_HOSTNAME}` (o `PROTOCOL_HEADER=x-forwarded-proto` +
  `HOST_HEADER=x-forwarded-host` si el proxy los inyecta).
- URLs **públicas** (navegador): `PUBLIC_API_URL`, `PUBLIC_KEYCLOAK_URL`.
- URLs **internas** (SSR → red Docker): `INTERNAL_API_URL`, `INTERNAL_KEYCLOAK_URL`.

El `redirect_uri` resultante (`https://${FRONTEND_HOSTNAME}/login/callback`) debe
estar en `redirectUris`/`webOrigins` del cliente `orpycamcp-frontend` en Keycloak.

### Rotación de secretos

Cada 90 días (o según política corporativa):

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

## Despliegue con Docker Compose

### Usando docker-compose.prod.yml

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
#    El script corre DESDE EL HOST, no dentro de un contenedor, y toma los tres
#    argumentos por posición: <slug> <nombre> <código>.
cd ..
COMPOSE_FILE=infra/docker-compose.prod.yml ENV_FILE=infra/.env \
  ./scripts/init-tenant.sh mi-institucion "Mi Institución" MIST
```

### Verificación post-despliegue

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

## Clustering y escalabilidad

Para instituciones grandes (> 10,000 usuarios):

### Opción 1: Docker Swarm (simple)

```bash
# Inicializar cluster
docker swarm init
docker swarm join --token <TOKEN> manager-node-ip

# Desplegar stack
docker stack deploy -c docker-compose.prod.yml orpyca-mcp
```

### Opción 2: Kubernetes (recomendado)

Convertir a Helm charts:

```bash
# Convertir docker-compose a Kubernetes
docker-compose -f docker-compose.prod.yml config | docker-to-k8s > orpycamcp-k8s.yaml

# Desplegar en cluster K8s
kubectl apply -f orpycamcp-k8s.yaml
```

### Escalabilidad de servicios

Servicios sin estado (stateless) pueden escalarse horizontalmente:

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

## Respaldos y recuperación

### Respaldo automático de PostgreSQL

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

Programar con cron:

```bash
# Ejecutar diariamente a las 2:00 AM
0 2 * * * /srv/orpyca/backup.sh
```

### Recuperación desde respaldo

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

## Monitoreo y logs

### Centralizando logs con ELK Stack

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

### Métricas con Prometheus

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

### Alertas

Configurar alertas para:

- CPU/memoria > 80%
- Espacio en disco < 20%
- PostgreSQL retrasos de replicación
- SMTP delivery failures
- Tasa de errores HTTP > 5%

---

## Seguridad

### Checklist de seguridad previa al despliegue

- [ ] Todos los servicios tienen TLS habilitado
- [ ] PostgreSQL no es accesible externamente
- [ ] MinIO requiere autenticación (sin acceso público)
- [ ] Redis requiere contraseña
- [ ] Keycloak usa HTTPS con certificado válido
- [ ] Firewall bloquea puertos internos (5432, 6379, 9000)
- [ ] CORS configurado restringidamente
- [ ] Audit logging habilitado en PostgreSQL y Keycloak
- [ ] Backup diario con pruebas de recuperación
- [ ] Rate limiting en api-gateway activado
- [ ] Headers de seguridad (HSTS, X-Content-Type-Options, etc.)

### Headers de seguridad en Nginx

```nginx
# infra/nginx.conf
add_header Strict-Transport-Security "max-age=31536000; includeSubDomains" always;
add_header X-Content-Type-Options "nosniff" always;
add_header X-Frame-Options "SAMEORIGIN" always;
add_header X-XSS-Protection "1; mode=block" always;
add_header Referrer-Policy "strict-origin-when-cross-origin" always;
```

### Auditoría de seguridad

```bash
# Escanear imágenes Docker en busca de vulnerabilidades
docker scan api-gateway:latest
docker scan postgres:15-alpine

# Usar Trivy para análisis de vulnerabilidades
trivy image api-gateway:latest
```

---

## Troubleshooting

### Los servicios no inician

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

### PostgreSQL rechaza conexiones

```bash
# Verificar autenticación
docker compose exec postgres psql -U orpycamcp -d orpycamcp_db -c "SELECT version();"

# Resetear contraseña si es necesario
docker compose exec postgres psql -U postgres -c \
  "ALTER USER orpycamcp PASSWORD 'new-password';"
```

### MinIO no se sincroniza con buckets

```bash
# Verificar estado de MinIO
docker compose exec minio mc status minio

# Listar buckets
docker compose exec minio mc ls minio/

# Recrear bucket si está corrupto
docker compose exec minio mc rb minio/orpycamcp-tenant-documents
docker compose exec minio mc mb minio/orpycamcp-tenant-documents
```

### Alto uso de memoria

```bash
# Reducir buffer_pool_size en PostgreSQL
docker compose down
# Editar docker-compose.prod.yml
sed -i 's/-c shared_buffers=256MB/-c shared_buffers=128MB/' docker-compose.prod.yml
docker compose up -d postgres
```

---

## Siguientes pasos

1. **Integrar con tu PKI corporativa** — usar certificados firmados internamente
2. **Implementar SSO corporativo** — federación SAML/OIDC con Entra ID, Okta, etc.
3. **Configurar respaldo en nube** — AWS S3, GCS, o Azure Blob Storage
4. **Implementar recuperación ante desastres** — replicación a un segundo sitio
5. **Entrenar al equipo** — cómo operacionalizar OrpycaMCP

---

**Preguntas?** Contacte a: aurigadl@gmail.com
