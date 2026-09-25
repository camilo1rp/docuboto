# Guía de Despliegue en Producción

> **IMPORTANTE**: Esta guía es un punto de partida. Consulte con especialistas en DevOps y seguridad de su institución antes de desplegar en producción.

## Tabla de contenidos

- [Requisitos previos](#requisitos-previos)
- [Configuración de infraestructura](#configuracion-de-infraestructura)
- [Secretos y variables de entorno](#secretos-y-variables-de-entorno)
- [Despliegue con Docker Compose](#despliegue-con-docker-compose)
- [Clustering y escalabilidad](#clustering-y-escalabilidad)
- [Respaldos y recuperación](#respaldos-y-recuperacion)
- [Actualización y vuelta atrás](#actualizacion-y-vuelta-atras)
- [Monitoreo y logs](#monitoreo-y-logs)
- [Seguridad](#seguridad)
- [Troubleshooting](#troubleshooting)

---

## Requisitos previos

### Hardware mínimo

Para una institución pequeña a mediana (< 5000 usuarios):

- **CPU**: 4 cores (x86-64 o ARM64)
- **RAM**: 16 GB
- **Almacenamiento**: 500 GB SSD para PostgreSQL + 1-5 TB para el almacén de objetos (según volumen de documentos)
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
sudo ufw deny 9000  # almacén de objetos (S3)

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
openssl rand -base64 32  # clave del almacén de objetos
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
| `MINIO_ROOT_PASSWORD` | Clave raíz del almacén de objetos (el prefijo `MINIO_` es histórico, ADR-027) | `<openssl rand -base64 32>` |
| `KEYCLOAK_HOSTNAME` | Dominio FQDN de Keycloak | `keycloak.yourdomain.com` |
| `KEYCLOAK_ADMIN_PASSWORD` | Contraseña admin Keycloak | `<secure password>` |
| `KEYCLOAK_*_SECRET` | Secrets de clientes OAuth2 | `<32 char hex>` |
| `FRONTEND_HOSTNAME` | FQDN público del frontend SSR (define `ORIGIN` y el `redirect_uri` del login) | `app.yourdomain.com` |
| `API_PUBLIC_HOSTNAME` | FQDN público del api-gateway que ve el navegador | `api.yourdomain.com` |
| `SIGNATURE_SECRET` | Clave HMAC del sellado de firma (E06). Aleatoria, rotar. **El servicio no arranca sin ella** | `<openssl rand -base64 32>` |
| `MINIO_ROOT_USER` | Usuario del almacén de objetos. **storage-service no arranca sin él** | `orpyca_prod` |
| `KNOWLEDGE_INTERNAL_TOKEN` | Token de ingesta interna (E21). Vacío ⇒ ingesta fail-closed | `<openssl rand -base64 32>` |
| `SMTP_HOST` | Host SMTP | `smtp.gmail.com` |
| `SMTP_PASSWORD` | Contraseña o token de aplicación SMTP | `app-specific-password` |

### Variables que impiden el arranque si faltan

Tres secretos tenían **valor por defecto funcional** en el código, publicado en un
repositorio AGPL: `SIGNATURE_SECRET` valía `change-me-in-production` y las
credenciales del almacén de objetos eran las de desarrollo que aparecen en el propio
`CLAUDE.md`. Un despliegue que se olvidara de definirlos **no fallaba**: arrancaba
y firmaba —o guardaba los anexos oficiales— con una clave que cualquiera puede
leer en GitHub.

Ahora el default es cadena vacía **y** el arranque lo comprueba: `signature-service`
y `storage-service` se niegan a servir sin `SIGNATURE_SECRET`,
`MINIO_ACCESS_KEY` y `MINIO_SECRET_KEY`. También se rechaza el valor de ejemplo
(`change-me-in-production` y similares), por si alguien copia el `.env.example`
sin tocarlo. El mensaje de error nombra la variable, nunca el valor recibido.

Si además se usa la ingesta de correo (E19), `EMAIL_ENCRYPTION_KEY` es
obligatoria: con ella se cifran en reposo las credenciales de los buzones. Ya no
hay variables `IMAP_*` — cada buzón se configura desde `/admin/correo` y vive en
`email_cuenta`. Para el sondeo automático, `EMAIL_SONDEO_TENANT_SLUG` declara
**a qué entidad pertenecen los buzones que sondea el proceso**; sin ella el
worker no arranca aunque `EMAIL_SONDEO_ENABLED` esté activo.

### CORS

`CORS_ALLOW_ORIGINS` está **vacío por defecto y el middleware no se instala**. No
es una regresión: el frontend habla con el gateway **desde el servidor** (SSR), no
desde el navegador, así que el despliegue típico no necesita CORS. Solo hay que
declararlo para una integración de terceros o un SPA en otro dominio:

```bash
CORS_ALLOW_ORIGINS=https://sgd.entidad.gov.co,https://portal.entidad.gov.co
```

> El comodín `*` se acepta pero **desactiva las credenciales**. La configuración
> anterior era `allow_origins=["*"]` **junto con** `allow_credentials=True`, en
> duro: con credenciales, Starlette no puede responder `*` y refleja el `Origin`
> recibido — cualquier página de internet podía llamar al gateway con las
> credenciales del visitante.

### Particiones del registro de auditoría

`public.audit_log` está declarado particionado por mes desde su primera migración,
pero la tarea que crea las particiones no existía: todo se acumulaba en
`audit_log_default`. Además de la lentitud, la trampa se cierra con el tiempo —
PostgreSQL **rechaza** crear una partición cuyo rango solape con filas que ya están
en el default.

La migración `005_audit_partition_job.sql` instala `ensure_audit_partitions(n)` y la
ejecuta una vez. Prográmala mensualmente:

```bash
# Crea (y endurece) las particiones de los próximos 3 meses. Idempotente.
docker compose -f infra/docker-compose.prod.yml run --rm --no-deps \
    -v "$PWD/scripts:/scripts:ro" auth-service \
    python /scripts/audit_partitions.py --meses 3

# Para la supervisión: 0 si están al día, 1 si falta la del mes siguiente.
... python /scripts/audit_partitions.py --verificar
```

Se adelanta varios meses a propósito: si el cron falla en silencio, hay margen
antes de que los asientos vuelvan a caer en la partición por defecto.

> El histórico que ya está en `audit_log_default` **no se mueve**. Reescribir filas
> de un registro append-only con cadena de hash es tocar la base probatoria de todo
> el archivo, y ordenar lo viejo no lo compensa. La función arranca en el primer mes
> que no solape: el default se congela y deja de crecer.

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

# Verificar que el almacén de objetos está funcionando
#
# Ya no hay cliente `mc` dentro del contenedor: el motor es RustFS (ADR-027) y
# MinIO retiró también su cliente de los registros. Se usa `aws-cli`.
docker compose exec minio curl -fsS http://localhost:9000/health

# Verificar que Redis está funcionando
docker compose exec redis redis-cli -a $REDIS_PASSWORD ping
```

#### Las dos sondas: `/health` y `/health/ready`

Cada servicio expone **dos** rutas, y no son intercambiables:

| Ruta | Pregunta | Comprueba | Quién la consulta |
|---|---|---|---|
| `/health` | ¿Responde el proceso? | Nada | El `healthcheck` del contenedor |
| `/health/ready` | ¿Puede atender tráfico **ahora**? | Base de datos, Redis, SMTP, consumidor de eventos | El balanceador / proxy reverso |

`/health` **no depende de nada a propósito**. Reiniciar un servicio porque
Postgres está caído no arregla Postgres: tira el trabajo en vuelo y suma una
tormenta de reinicios a un incidente en curso. `/health/ready` devuelve **503**
cuando el servicio no puede trabajar, que es la señal para que el balanceador
deje de mandarle peticiones **sin reiniciarlo**.

Configure el proxy reverso para sacar del reparto a un servicio cuyo
`/health/ready` responda 503; deje los reinicios a `/health`.

```bash
# Diagnóstico agregado del sistema (gateway + sus diez servicios)
curl -s https://sudominio.gov.co/health/ready | jq

# Estado del consumidor de eventos de un servicio concreto
docker compose exec workflow-service \
  python -c "import urllib.request,json; print(json.load(urllib.request.urlopen('http://localhost:8006/health/ready')))"
```

En `checks.consumer`, el valor `reiniciando` significa que el consumidor de
eventos murió y está reintentando: el servicio atiende peticiones, pero lo que
ese consumidor hace —repartir la correspondencia, enviar correos, sellar firmas,
alimentar el buscador— **no está ocurriendo**. Es motivo de revisión aunque el
servicio figure disponible.

Las sondas informan el estado con una palabra (`ok`, `error`, `unreachable`) y
nunca el detalle del fallo: son alcanzables sin autenticar y el texto de una
excepción de red incluye nombres y puertos internos. El motivo real está en el
log del servicio.

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

> **Los scripts existen y están probados.** `infra/backup.sh` y `infra/restore.sh`
> son ficheros ejecutables del repositorio, no fragmentos para copiar y pegar. La
> versión anterior de esta sección era un bloque de bash que **nunca se había
> ejecutado**: usaba el usuario de desarrollo, volcaba el almacén a una ruta que ningún
> contenedor monta con un cliente que esa imagen no trae, no llevaba
> `set -euo pipefail` —así que un volcado fallido producía un `.gz` vacío pero
> válido— y purgaba los respaldos de más de 30 días **pase lo que pase**. En cron,
> a las 2 de la mañana, eso es una rotación de respaldos vacíos con apariencia de
> normalidad. Y el procedimiento de recuperación hacía `docker compose down` y acto
> seguido `docker compose exec postgres`, sobre un contenedor que ya no existía.

### Respaldo

```bash
ENV_FILE=infra/.env BACKUP_DIR=/srv/orpyca/backups ./infra/backup.sh
```

Vuelca PostgreSQL (todos los schemas: `public` con el registro de tenants y el
`audit_log`, más un `tenant_*` por institución) y espeja el almacén. Tres garantías que
conviene conocer porque cambian cómo se opera:

1. **El volcado se verifica antes de darse por bueno** (`pg_restore --list` parsea
   la cabecera y el índice completo). Un fichero truncado falla aquí, no seis meses
   después.
2. **La purga de respaldos antiguos solo ocurre si esa verificación pasó.** Un
   respaldo que no se puede leer no autoriza a borrar el anterior.
3. Un respaldo interrumpido se renombra a `*.PARCIAL` y **no** lleva el fichero
   sello `OK`. `restore.sh` se niega a usar un directorio sin ese sello.

Formato `custom` (`-Fc`): ya viene comprimido, permite restauración selectiva y
paralela, y —lo que hace posible el punto 1— puede inspeccionarse sin restaurarlo.

En cron, redirigiendo a un log **que alguien mire**:

```bash
0 2 * * * ENV_FILE=/srv/orpyca/infra/.env /srv/orpyca/infra/backup.sh \
            >> /var/log/orpyca-backup.log 2>&1
```

### Restauración

```bash
ENV_FILE=infra/.env ./infra/restore.sh /srv/orpyca/backups/20260808_020000
```

Para las aplicaciones y Keycloak pero **deja en pie Postgres y el almacén**, que son el
destino del trabajo. Exige confirmación escrita: restaurar destruye el estado
actual y es irreversible.

**El paso que convierte esto en una restauración probada** es el último: recalcula
la **cadena de hash del `audit_log`** por tenant y compara cada asiento con su
enlace anterior. Una base que arranca no prueba nada sobre la integridad de la
traza legal, que es justo lo que un archivo tiene que poder demostrar. Si la cadena
está rota, el script lo dice con el `id` del primer asiento afectado.

Dos cosas que no son evidentes:

- Los objetos bajo **Object-Lock en modo COMPLIANCE no se borran** aunque el
  respaldo no los contenga, ni por root, hasta que venza su retención. Es lo
  correcto: la inmutabilidad WORM está por encima de esta restauración.
- Tras restaurar, comprueba **un radicado con anexo: consúltalo y descárgalo**. Es
  la única prueba que ejercita la base y el almacén de objetos a la vez; con una
  sola de las dos, una restauración a medias parece completa.

### Ensayo de restauración

Un respaldo cuya restauración no se ha probado no es un respaldo: es un fichero.
El ciclo completo —volcar, destruir, restaurar, verificar la cadena— se ha
ejercitado contra contenedores reales, y la verificación se comprobó **por
mutación**: manipulando un asiento con el trigger de append-only desactivado, la
cadena se reporta rota y el script sale con código distinto de cero.

Repítelo en tu instalación al menos una vez, y después de cada actualización mayor.

---

## Actualización y vuelta atrás

> **No existía procedimiento.** Ni sección de actualización, ni comando para migrar
> tenants ya dados de alta —el único ejecutor es `scripts/init_tenant.py`, pensado
> para el ALTA y con el árbol de fuentes montado—, ni tag inmutable al que volver:
> el manifiesto construye desde el fuente en el propio servidor, así que «la versión
> anterior» no era una cosa que existiera en ningún sitio.

### Antes de nada: la versión tiene que ser una cosa

Mientras el despliegue use `build:` contra el árbol de fuentes, **no hay vuelta
atrás posible**: volver significa `git checkout` y reconstruir, con el resultado
sujeto a lo que haya en el registro de paquetes ese día. El requisito previo es
publicar imágenes con un tag inmutable (el SHA del commit) y que el manifiesto use
`image:` en vez de `build:`.

`infra/DOCKER_REGISTRY.md` documenta un pipeline de GitHub Actions que **no
existe** en este repositorio (`.github/` no está). El trabajo de publicar imágenes
por servicio con `$CI_COMMIT_SHA` pertenece al pipeline de GitLab.

### Procedimiento

```bash
# 1. RESPALDO VERIFICADO. No "un respaldo": uno que terminó con el sello OK.
ENV_FILE=infra/.env BACKUP_DIR=/srv/orpyca/backups ./infra/backup.sh

# 2. Anotar la versión actual — es a lo que se vuelve si algo sale mal.
docker compose -f infra/docker-compose.prod.yml images | tee /srv/orpyca/version-anterior.txt

# 3. Traer la nueva versión (sin arrancarla todavía).
export ORPYCA_TAG=<sha-del-commit>
docker compose -f infra/docker-compose.prod.yml pull

# 4. Migraciones. Cada servicio aplica las suyas y el runner recorre TODOS los
#    schemas tenant_* que encuentre, así que un tenant nuevo no se queda atrás.
#    Son idempotentes por convención (ADR-012): re-ejecutarlas es seguro.
#    OJO: es `python migrations/runner.py`, un script — NO `python -m
#    app.migrations.runner`, que es un módulo que no existe.
for svc in auth tenant document archive storage workflow notification signature knowledge; do
  docker compose -f infra/docker-compose.prod.yml run --rm --no-deps \
    "${svc}-service" python migrations/runner.py
done

# 5. Desplegar.
docker compose -f infra/docker-compose.prod.yml up -d

# 6. Comprobar. Los tres que cubren base, almacén e identidad a la vez:
#    - login de un usuario real
#    - un radicado con anexo: consultarlo y DESCARGARLO
#    - GET /api/v1/audit/verify de un tenant
```

### Criterio de vuelta atrás

Vuelve atrás si tras el paso 6 falla el login, la descarga de un anexo o la
verificación de la cadena de auditoría. No esperes a "ver si se estabiliza": el
coste de volver crece con cada radicado que entra.

```bash
export ORPYCA_TAG=<sha-anterior>
docker compose -f infra/docker-compose.prod.yml up -d
```

**Volver el CÓDIGO no vuelve el ESQUEMA.** Las migraciones no tienen `down`, y es
deliberado: una bajada automática sobre datos de archivo es más peligrosa que el
fallo que intenta reparar. Si la versión nueva cambió el esquema de forma
incompatible, la vuelta atrás real es **restaurar el respaldo del paso 1** con
`infra/restore.sh`, asumiendo la pérdida de lo ocurrido desde entonces. Por eso el
paso 1 no es opcional y por eso conviene actualizar en ventana de baja actividad.

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
- [ ] El almacén de objetos requiere autenticación (sin acceso público)
- [ ] Redis requiere contraseña
- [ ] Keycloak usa HTTPS con certificado válido
- [ ] Firewall bloquea puertos internos (5432, 6379, 9000)
- [ ] CORS configurado restringidamente
- [ ] Audit logging habilitado en PostgreSQL y Keycloak
- [ ] Backup diario con pruebas de recuperación
- [ ] Rate limiting en api-gateway activado
- [ ] Headers de seguridad (HSTS, X-Content-Type-Options, etc.)

### Cabeceras de seguridad en Nginx

Están en `infra/nginx.conf.example`, que hasta ahora esta guía mandaba copiar sin
que el archivo existiera. Lo importante de esa plantilla no son las cabeceras: son
las tres cosas que dependen de `X-Forwarded-*` y fallan en silencio sin ellas — el
`redirect_uri` del login PKCE, la IP real que se sella en `audit_log` (registro con
valor probatorio) y el reparto del límite por IP. Lleva también `proxy_buffering off`,
sin el cual nginx vuelve a acumular en disco cada descarga que el sistema sirve por
trozos (anexos, ZIP de expediente, AIP).

**HSTS va en el proxy, no en la aplicación.** Es una promesa sobre el dominio entero
y quien puede hacerla es quien termina el TLS; el frontend SSR no la emite por eso
mismo — si la emitiera un despliegue servido por `http`, dejaría el dominio
inalcanzable durante un año. Las demás cabeceras (CSP, `nosniff`, `Referrer-Policy`,
`X-Frame-Options`, `Permissions-Policy`) **las emite el propio SSR**: no las
dupliques en nginx, porque `add_header` no sustituye la del backend y dos valores
distintos de la misma cabecera dan un comportamiento que depende del navegador.

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

### El almacén de objetos no se sincroniza con buckets

El motor es **RustFS** desde el ADR-027 y no incluye el cliente `mc`, así que estas
comprobaciones se hacen con `aws-cli` desde un contenedor efímero en la misma red.

```bash
# Alias para no repetir el invocatorio
alias s3orp='docker run --rm --network orpycamcp-net \
  -e AWS_ACCESS_KEY_ID="$MINIO_ROOT_USER" \
  -e AWS_SECRET_ACCESS_KEY="$MINIO_ROOT_PASSWORD" \
  amazon/aws-cli --endpoint-url http://minio:9000'

# Salud del almacén
docker compose exec minio curl -fsS http://localhost:9000/health

# Listar buckets
s3orp s3 ls

# Recrear un bucket: **con Object Lock**, o deja de ser un bucket de preservación.
# El bloqueo SOLO se puede habilitar al crear — no hay forma de activarlo después
# sin perder el contenido.
s3orp s3api create-bucket --bucket orpycamcp-tenant-documents \
  --object-lock-enabled-for-bucket
```

> **No borre un bucket de preservación para «recrearlo limpio».** Sus objetos están
> bajo retención en modo COMPLIANCE: no se pueden borrar ni reescribir, y si lo
> consigue es que el almacén no está aplicando la garantía que el acta afirma.

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
