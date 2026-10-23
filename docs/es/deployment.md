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
- Linux (RHEL, Ubuntu, Debian). Kubernetes no está soportado todavía (ver [Opción 2](#opcion-2-kubernetes-no-soportado-todavia))
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

### 2. Obtener certificados TLS

El servicio `edge` (nginx en contenedor, §4) monta `/etc/letsencrypt` del servidor en solo lectura y
necesita **un certificado por dominio**: `FRONTEND_HOSTNAME`, `API_PUBLIC_HOSTNAME` y
`KEYCLOAK_HOSTNAME`. Con Let's Encrypt (gratuito, requiere el puerto 80 libre y los tres nombres
resolviendo al servidor), la **primera emisión** va con `--standalone` y **antes del primer `up`** (el
puerto 80 aún no lo ocupa nadie):

```bash
sudo apt install certbot
sudo certbot certonly --standalone -d app.yourdomain.com
sudo certbot certonly --standalone -d api.yourdomain.com
sudo certbot certonly --standalone -d keycloak.yourdomain.com
# Certificados en /etc/letsencrypt/live/<dominio>/{fullchain,privkey}.pem
```

Las **renovaciones** ya no pueden usar `--standalone` (el puerto 80 lo tiene `edge`): van por *webroot*
sobre `infra/certbot-www` (que `edge` sirve en `/.well-known/acme-challenge/`) y recargan nginx al
terminar:

```bash
mkdir -p infra/certbot-www
sudo certbot renew --webroot -w "$PWD/infra/certbot-www" \
  --deploy-hook "docker compose -f $PWD/infra/docker-compose.prod.yml --env-file $PWD/infra/.env exec edge nginx -s reload"
```

Con CA corporativa: deje `fullchain.pem` y `privkey.pem` de cada dominio en
`/etc/letsencrypt/live/<dominio>/` (o monte su propia ruta en el servicio `edge`).

### 3. Configurar firewall

```bash
# Asumir UFW en Ubuntu
sudo ufw default deny incoming
sudo ufw default allow outgoing

# Permitir SSH (CRÍTICO: no bloquee tu acceso)
sudo ufw allow ssh

# Permitir el proxy de entrada (servicio `edge`)
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp

sudo ufw enable
```

> **Docker se salta ufw en los puertos que publica.** Un `ports:` en el compose abre el puerto
> en `iptables` por delante de las reglas de ufw, así que `ufw deny 5432` **no protege** un
> PostgreSQL publicado. La defensa real es no publicarlos: el manifiesto de producción publica
> **solo** `80` y `443`, y solo en el servicio `edge`; un guardián
> (`shared/tests/test_manifiesto_prod_seguro.py`) falla si cualquier otro servicio publica un
> puerto. Compruébelo desde fuera con `scripts/preflight-prod.sh`.

### 4. Entrada de producción: servicio `edge` (nginx en contenedor)

La entrada la hace el servicio `edge` de `infra/docker-compose.prod.yml` (ADR-033): nginx en
contenedor, **el único servicio con `ports:`** (`80:80`, `443:443`). No se instala nginx en el
servidor. La plantilla es `infra/nginx/orpyca.conf.template`; la imagen oficial sustituye
`${FRONTEND_HOSTNAME}`, `${API_PUBLIC_HOSTNAME}` y `${KEYCLOAK_HOSTNAME}` al arrancar, así que **no hay
nada que copiar ni editar**: los tres dominios salen del mismo `infra/.env`.

Por qué en contenedor y no en el servidor:

- `api-gateway` y `frontend` tienen `deploy.replicas: 2`: publicar un puerto fijo del servidor lo
  tomaría la primera réplica y la segunda moriría con «port is already allocated». nginx resuelve
  el nombre del servicio en cada petición (`resolver 127.0.0.11`) y reparte entre réplicas.
- Con nginx en el servidor, el gateway vería como par a la IP del docker-proxy
  (ver `TRUSTED_PROXIES`, abajo).
- Los servicios llaman a `https://${KEYCLOAK_HOSTNAME}` desde dentro de la red: `edge` lleva ese
  nombre (y los otros dos) como **alias** en `orpycamcp-net`, así que el DNS de Docker lo resuelve al
  propio `edge`, con certificado válido, sin *hairpin* NAT ni DNS externo. Eso es lo que hace funcionar
  el login PKCE del SSR (`INTERNAL_KEYCLOAK_URL`).

Qué publica cada dominio:

| Dominio | Destino | Nota |
|---|---|---|
| `FRONTEND_HOSTNAME` | `frontend:3000` | HSTS; el resto de cabeceras las emite el SSR |
| `API_PUBLIC_HOSTNAME` | `api-gateway:8080` | `/health/ready` solo desde el propio contenedor `edge` (`127.0.0.1`); sondeo: `docker exec <edge> wget --no-check-certificate -qO- --header 'Host: <API_PUBLIC_HOSTNAME>' https://127.0.0.1/health/ready`. La IP del gateway de la red (`172.20.0.1`) **no** se admite: es la que ve nginx para cualquier cliente de internet |
| `KEYCLOAK_HOSTNAME` | `keycloak:8080` | lista de **admisión**: solo `/realms/orpycamcp/` y `/resources/`; todo lo demás (incluidos `/admin/`, `/realms/master`, `/realms/master;x/…`) responde 404, y una ruta con `;` también |

La consola de administración de Keycloak **no se publica**: úsela por túnel SSH
(`ssh -L 8080:localhost:8080 servidor`, hacia el puerto del contenedor) o con
`docker compose exec keycloak`.

```bash
cd infra
docker compose -f docker-compose.prod.yml --env-file .env config -q   # valida el manifiesto
docker compose -f docker-compose.prod.yml --env-file .env up -d
docker compose -f docker-compose.prod.yml --env-file .env exec edge nginx -t
```

### IP verificada de la ventanilla pública (`TRUSTED_PROXIES`)

La constancia de autorización de la ventanilla (Ley 1581/2012, ADR-031) guarda la IP del cliente **solo si el gateway puede afirmarla**. El gateway la deriva recorriendo `X-Forwarded-For` **desde la derecha** y descartando los saltos de `TRUSTED_PROXIES`; la firma con `IDENTITY_SIGNING_SECRET` (ligada a la ruta, válida 120 s) y document-service solo acepta esa firma: **nunca** lee `X-Forwarded-For`. Para que `ip_verificada` sea `true` en producción:

1. `IDENTITY_SIGNING_SECRET` configurado (es obligatorio en producción; sin él la IP queda nula).
2. `TRUSTED_PROXIES` del api-gateway debe incluir **la red del `edge` y la del frontend SSR** (los dos saltos que encadenan `X-Forwarded-For`: nginx usa `proxy_add_x_forwarded_for` y el SSR reenvía la IP del navegador). El valor por defecto del manifiesto es **`172.20.128.0/17`**: el `ip_range` de `orpycamcp-net` (la subred es `172.20.0.0/16`, pero los contenedores reciben IP solo de la mitad alta). Si falta alguna, el gateway ve como cliente al propio proxy: la constancia registra la IP del proxy, no la de la persona.

   **Por qué el gateway de la red (`172.20.0.1`) queda fuera.** nginx *añade* `$remote_addr` a lo que mande el cliente en `X-Forwarded-For`. Si `$remote_addr` llegara a ser `172.20.0.1` (un cliente IPv6 servido por el docker-proxy, o un proceso del propio servidor) y esa IP fuera de confianza, el gateway de la API la saltaría y se quedaría con la entrada **forjada por el cliente**, que acabaría sellada en `audit_log` y en la constancia (ADR-031). Con el gateway de la red fuera del rango de confianza el resultado es *fail-closed*: se registra `172.20.0.1`, nunca una IP inventada. Un guardián (`test_trusted_proxies_excluye_el_gateway_de_la_red`) impide volver a un `/16`. Si cambia la subred de la red en el compose, cambie `TRUSTED_PROXIES` a la par.

   **IPv6.** Docker entrega los clientes IPv6 por su docker-proxy, que oculta la IP real (se vería `172.20.0.1`). Para conservarla active `"ip6tables": true` en `/etc/docker/daemon.json`; sin eso, la IP de un cliente IPv6 queda como `172.20.0.1` (no informa, pero no se puede forjar).
3. Con `TRUSTED_PROXIES` vacío (el valor por defecto) la cabecera se ignora y se usa la dirección del socket: es seguro, pero no informa.

**Importante:** la IP solo es fiable si el frontend es alcanzable **únicamente a través de `edge`**. Un puerto del SSR expuesto directamente (en desarrollo, `19300` con `XFF_DEPTH=1`) acepta un `X-Forwarded-For` forjado por el cliente y lo encadena como si fuera de confianza; en producción ningún servicio salvo `edge` publica puertos (guardián del manifiesto).

Si su `infra/.env` viene de una versión anterior y fija `TRUSTED_PROXIES=172.20.0.0/16`, **cámbielo** a `172.20.128.0/17` (o bórrelo): con el `/16` el gateway de la red vuelve a ser de confianza.

**El gateway de la API se niega a arrancar si se lo deja así (ARC-B4).** El manifiesto de producción define `APP_ENV=production` y `GATEWAY_RED_DOCKER_GATEWAY=172.20.0.1` (debe coincidir con `gateway:` de la red; un guardián lo vigila); si `TRUSTED_PROXIES` cubre esa IP el arranque termina con un error que lo explica. Si la variable estuviera vacía, la IP se deriva de la ruta por defecto del contenedor. **Solo en producción:** en desarrollo `docker-compose.yml` fija `172.16.0.0/12` (que sí la contiene) a propósito y no se comprueba.

Sin firma válida la constancia dice `ip=null, ip_verificada=false` y **no se bloquea** la radicación.

### Política de tratamiento de datos de cada entidad

La ventanilla pública **no radica** hasta que la entidad publique su aviso de privacidad (409 `politica_no_publicada`): defina `politica_tratamiento_version`, `politica_tratamiento_url` y/o `politica_tratamiento_texto` en `/admin/parametros` (administrador nivel 3). Parta de la [plantilla](politica-tratamiento-plantilla.md). `./scripts/verificar-ventanilla.sh <slug>` sale ≠ 0 si falta (`SIN POLITICA`).

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
| `ORPYCA_REGISTRY` / `ORPYCA_TAG` | Registro de imágenes y tag inmutable a desplegar (el SHA del commit; nunca `latest`). **Obligatorias** (`:?`): sin ellas ningún comando `docker compose` arranca | `registry.example.org/orpyca` / `<sha>` |
| `FRONTEND_HOSTNAME` | FQDN público del frontend SSR (define `ORIGIN` y el `redirect_uri` del login) | `app.yourdomain.com` |
| `API_PUBLIC_HOSTNAME` | FQDN público del api-gateway que ve el navegador | `api.yourdomain.com` |
| `SIGNATURE_SECRET` | Clave HMAC del sellado de firma (E06). Aleatoria, rotar. **El servicio no arranca sin ella** | `<openssl rand -base64 32>` |
| `MINIO_ROOT_USER` | Usuario del almacén de objetos. **storage-service no arranca sin él** | `orpyca_prod` |
| `WEBHOOK_SECRET_KEK` | KEK (base64 de 32 bytes) que cifra en reposo el secreto HMAC de los webhooks. **notification-service no arranca sin ella**; distinta de las demás KEK | `<openssl rand -base64 32>` |
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

**`ALTCHA_HMAC_KEY` (C16, ADR-038) es obligatoria**: clave (≥ 32 bytes; `openssl rand -hex 32`) con la que document-service firma los retos antirrobots de la ventanilla pública anónima. El manifiesto de producción la exige (`${ALTCHA_HMAC_KEY:?}`) y el servicio **no arranca** sin ella o con menos de 32 bytes. Nunca llega al navegador; rotarla solo invalida los retos vivos (≤ `ALTCHA_TTL_SEGUNDOS`, 300 por omisión). `ALTCHA_MAXNUMBER` (100000) acota el costo del cliente. Si Redis cae, la ventanilla responde `503 antirrobots_no_disponible` (fail-closed: sin Redis no se puede garantizar el uso único del reto).

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

**El fallo del cron se ve sin leer ningún log.** `GET /health/ready` de auth-service
aplica la misma lógica de `--verificar` (¿existe la partición del mes siguiente?) en
solo lectura y con el rol de la aplicación: si falta, responde **200** con
`"degraded": ["audit_particiones"]` y `"audit_particiones": "degraded"` (es una alerta
de operación, no saca el servicio del balanceador); si no puede consultar, `unknown`
y no acusa. Alerta sobre ese campo en la supervisión que ya tengas.

### Verificación diaria de la cadena de auditoría

`verify_chain` solo corría bajo demanda (`GET /api/v1/audit/verify`) o al restaurar.
Prográmala **diaria**, con el módulo que usan también `backup.sh` y `restore.sh`:

```bash
# Sale con 0 si TODAS las cadenas son íntegras y ninguna retrocedió respecto al último
# ancla; con 1 si hay una rotura, un retroceso o un ancla ilegible. Solo LEE.
docker compose -f infra/docker-compose.prod.yml run --rm --no-deps -T \
    -e "DATABASE_URL=postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@postgres:5432/${DB_NAME}" \
    auth-service python -m app.ops.verify_audit_chains \
    --anclas - < /srv/orpyca/backups/anclas-auditoria.tsv
```

Recorre `public.tenants`, todo `tenant_slug` presente en `audit_log` (incluido el
`__system__`) y los del ancla; por cada uno recalcula la cadena, exige que el conteo no
baje y que el hash del asiento anclado no cambie. **`backup.sh` lo invoca antes de
anclar y no ancla si falla** (fail-closed: sellar una cadena ya rota como «extremo de
confianza» es peor que no anclar), **pero el respaldo se completa**: se cifra, se sella
con `verificacion_auditoria=FALLIDA` y el script sale con código **3** al final (la
supervisión debe alertar con 3 y con 4; un `.PARCIAL` en claro por esta causa ya no
existe). `audit_log` solo admite anexar (Ley 594/2000), así que una rotura histórica no
se repara: el responsable la **declara** en una línea base
(`BACKUP_AUDIT_ROTURAS_DECLARADAS=/srv/orpyca/backups/roturas-declaradas.tsv`, líneas
`ROTURA<TAB>slug<TAB>id<TAB>nota` aprobadas por acta) y solo una rotura NUEVA, un
retroceso o un cambio del hash anclado vuelven a fallar. Si la retención del almacén no
se pudo leer por completo, el sello lleva `retencion_capturada_completa=NO` y el script
sale con **4**; `restore.sh` rechaza ese respaldo salvo
`RESTORE_ACEPTAR_RETENCION_INCOMPLETA=1`. `restore.sh` lo reutiliza en lugar del heredoc anterior. En desarrollo,
`BACKUP_AUDIT_EXCLUIR_PREFIJOS="itest"` deja fuera los tenants desechables que dejaron
los tests de integración (el registro es append-only y no se pueden borrar); **no lo
definas en producción**.

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

### KEK de los secretos de webhooks (rotación)

El secreto HMAC con el que notification-service firma cada entrega de webhook se guarda
**cifrado** (AES-256-GCM; AAD `tenant:{slug}:webhook:{id}:secret`) con `WEBHOOK_SECRET_KEK`, que
vive en el entorno del servicio y **nunca en la base**. Es el mismo esquema que la custodia de la
firma personal (`orpycamcp_common.kek`). Respáldala con la misma disciplina que el resto de KEK:
**si se pierde, los secretos son irrecuperables** y cada webhook con secreto hay que volver a
darlo de alta. Mientras una fila siga en claro o no descifre, **no se entrega** (fail-closed).

Rotación sin pérdida (conviven v1 y v2 durante la transición):

```bash
# 1. Añadir la KEK nueva al llavero y activarla; reiniciar notification-service.
#    WEBHOOK_SECRET_KEKS='{"2":"<openssl rand -base64 32>"}'
#    WEBHOOK_SECRET_KEK_ACTIVE_VERSION=2
# 2. Re-envolver todos los secretos a la KEK activa (todos los tenants).
docker compose -f infra/docker-compose.prod.yml run --rm --no-deps \
  notification-service python -m app.ops.rewrap_webhook_secrets --dry-run
docker compose -f infra/docker-compose.prod.yml run --rm --no-deps \
  notification-service python -m app.ops.rewrap_webhook_secrets
# 3. Verificar: una versión con conteo 0 es retirable (retirarla es manual).
docker compose -f infra/docker-compose.prod.yml run --rm --no-deps \
  notification-service python -m app.ops.rewrap_webhook_secrets --verify
```

Cada fila se re-cifra con nonce nuevo y round-trip antes de persistir y deja el asiento
`notification.webhook.secret_rewrapped` (solo versiones, nunca el secreto). Idempotente y
reanudable: si falla a medias, volver a ejecutarlo recoge el remanente.

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

### Identidad de un solo uso (Redis obligatorio) y `search_path` (ADR-044)

La identidad que el gateway firma para cada servicio es de **un solo uso**: lleva un nonce de 128 bits (`X-Identity-Nonce`, formato de firma **v3**) que el servicio de destino reclama en Redis (`SET NX EX`, clave `orpycamcp:identidad:nonce:v3:{tenant}:{nonce}`, vida ≤ 180 s). Consecuencias de operación:

- **Redis es dependencia dura de toda petición con identidad de persona.** Con Redis caído esas peticiones responden `503 identidad_no_verificable` (`Retry-After: 1`); la ventanilla pública, `/health`, los webhooks públicos y las llamadas internas sin `X-User-Id` siguen vivas. **`storage-service` ahora también necesita `REDIS_URL`** (y `depends_on: redis`); los diez componentes con `IDENTITY_SIGNING_SECRET` lo necesitan. Un servicio con el secreto y sin `REDIS_URL` **no arranca** (`lifespan.startup.failed`). No configure `maxmemory-policy allkeys-*` en ese Redis: podría desalojar un nonce y reabrir la reproducción (el valor por defecto, `noeviction`, es el correcto). En producción conserve `--appendonly yes`; sin él, un reinicio de Redis olvida hasta 180 s de nonces.
- **Corte atómico.** Pasar de v2 a v3 cambia la base del HMAC y no hay rama de compatibilidad: reconstruya y arranque **a la vez** el gateway y los nueve servicios (`docker compose up -d --build api-gateway auth-service tenant-service document-service archive-service storage-service workflow-service notification-service signature-service knowledge-service`). Si conviven versiones, toda petición autenticada responde 401.
- **`search_path` = solo el tenant.** Este cambio **no** requiere corte atómico: aplique las migraciones (`migrate-<servicio>`) y luego reconstruya los servicios. Las extensiones `pg_trgm`, `unaccent`, `pgcrypto` y `vector` **deben vivir en `public`**; las migraciones globales `document/005` y `knowledge/002` abortan con un mensaje claro si no es así. La migración `tenant-service/global/005` retira las 14 tablas de tenant que habían caído en `public`.
- Verificación en la pila: un GET autenticado por el gateway responde 200; la misma petición reproducida directo al servicio con las cabeceras capturadas, 401; con `docker compose stop redis`, el GET autenticado responde 503 y `/health` y una ruta pública, 200.

### Opción 2: Kubernetes (no soportado todavía)

**El repositorio no trae manifiestos de Kubernetes ni charts de Helm** (no hay `Chart.yaml` ni
`kustomization`), y la conversión automática desde Compose no se ha probado: los `migrate-<servicio>`,
las dependencias `service_completed_successfully` y los secretos por archivo no se traducen solos.
La vía soportada es Docker Compose (Opción 1) o Swarm. Si escala réplicas en cualquier orquestador,
**revise antes el estado en memoria**: el limitador del api-gateway cuenta en Redis, pero los
historiales del asistente, el consumo por `jti` de las propuestas del MCP y otros cachés son por proceso.

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
4. **La cadena de auditoría se verifica antes de anclarse** (ver «Verificación diaria»):
   un respaldo con una cadena rota o retrocedida **falla** y no escribe ancla.
5. **La retención del almacén** (Object-Lock) se guarda por **cada versión** de cada
   objeto —instante exacto de `RetainUntilDate` y retención legal (`LegalHold`)— en
   `minio-retencion.tsv`.

Formato `custom` (`-Fc`): ya viene comprimido, permite restauración selectiva y
paralela, y —lo que hace posible el punto 1— puede inspeccionarse sin restaurarlo.

En cron, redirigiendo a un log **que alguien mire**:

```bash
0 2 * * * ENV_FILE=/srv/orpyca/infra/.env /srv/orpyca/infra/backup.sh \
            >> /var/log/orpyca-backup.log 2>&1
```

### Restauración

```bash
RESTORE_OPERADOR="Nombre Apellido" ENV_FILE=infra/.env ./infra/restore.sh /srv/orpyca/backups/20260808_020000
```

`RESTORE_OPERADOR` es **obligatorio** (la persona que ejecuta la restauración; el script termina antes de confirmar o parar nada si falta): el acta `storage.restauracion` de `audit_log` se atribuye a `operador:<nombre>` y declara, por tenant, la **ventana de traza perdida** —el extremo de `audit_log` (conteo, id y hash del último asiento) se anota *antes* de `pg_restore` y se compara con el restaurado—. Restaurar un respaldo anterior al estado vivo suprime legalmente los asientos posteriores; el acta dice cuáles `(desde_id, hasta_id]` y cuántos.

Para las aplicaciones y Keycloak pero **deja en pie Postgres y el almacén**, que son el
destino del trabajo. Exige confirmación escrita: restaurar destruye el estado
actual y es irreversible.

**El paso que convierte esto en una restauración probada** es el último: recalcula
la **cadena de hash del `audit_log`** por tenant y compara cada asiento con su
enlace anterior. Una base que arranca no prueba nada sobre la integridad de la
traza legal, que es justo lo que un archivo tiene que poder demostrar. Si la cadena
está rota, el script lo dice con el `id` del primer asiento afectado.

**La retención se repone y se cuenta (fail-closed).** Tras sincronizar los objetos,
`restore.sh` repone la retención por plazo y la retención legal de la versión actual
de cada objeto, y **termina con error** si queda sin reponer una sola retención vigente
o retención legal activa (objetos borrables con un mensaje verde es el defecto que esto
impide). Una retención ya vencida se deja como está. Las versiones **previas** con
retención no se pueden reponer (el espejo solo copia la versión actual): si alguna está
bajo **COMPLIANCE vigente** (p. ej. un índice o un AIP re-sellado sobre la misma clave)
la restauración **falla** —esa evidencia se perdería— salvo que el responsable la acepte
con `RESTORE_ACEPTAR_PERDIDA_VERSIONES=1` (y la anote en el acta). Como tras restaurar el
`version_id` de `preservacion_worm_objeto` ya no corresponde a ningún objeto, el script
escribe `mapa-version-id-<fecha>.tsv` junto a los respaldos y **registra el acto**:
`app.ops.registrar_restauracion` actualiza `preservacion_worm_objeto.version_id` desde el
mapa y deja el asiento `storage.restauracion` (hash del mapa, recuentos y previas no
restauradas) en `audit_log`, en una sola transacción. Si ese paso falla, el script termina
con error: registra el acta a mano con el mapa antes de dar el sistema por bueno.

**Las versiones previas protegidas se respaldan y se restauran (B4).** Además del espejo,
`backup.sh` copia el contenido de cada versión **previa** con COMPLIANCE vigente o retención
legal a `minio-versiones/<bucket>/<sha256(clave+version)>` y la anota en
`minio-versiones.tsv` (fichero aparte: `minio-retencion.tsv` no cambia de formato); viaja
dentro del tar cifrado y el sello lleva `versiones_previas_capturadas=SI|NO` (**salida 5**
si no quedó completa). `restore.sh`, antes del `sync`, las sube de la más antigua a la más
reciente, verifica el sha256, repone la retención sobre el `VersionId` nuevo y sube con
`put-object` explícito la versión actual de esas claves; el mapa viejo→nuevo las incluye y
`registrar_restauracion` deja un evento PREMIS `REPLICA` (`motivo=restauracion`) por cada
objeto remapeado, en la misma transacción, con `hash_after` NULL y `detalle.fixity="no_verificada"`
(no relee el objeto restaurado: no declara una medición que no hizo). Si la versión actual de una
clave era un delete marker, el respaldo lo declara (`MARCADOR`) y la restauración repone el borrado;
una clave del respaldo con `..`, ruta absoluta o caracteres de control se rechaza. `RESTORE_ACEPTAR_PERDIDA_VERSIONES=1` queda solo
para respaldos anteriores a esto o sellados con `versiones_previas_capturadas=NO`.
`verify_audit_chains` sale **2** si no hay nada que verificar (filtro vacío o sin tenants):
`--permitir-objetivo-vacio` es solo para una instalación nueva y los scripts no lo pasan.

**Fail-closed (B7).** `restore.sh` termina con error —y deja los servicios parados— si
`pg_restore` falla por cualquier motivo: solo se admite el error inocuo de las PK heredadas de las
particiones de `audit_log` («cannot drop inherited constraint»), y **solo si** el código de salida es
exactamente 1, hay al menos una de esas PK y la línea final `errors ignored on restore: N` declara
N igual a ese número (cualquier otro código, o un N mayor —errores sin línea propia—, es fallo); una
salida ≠ 0 *sin* líneas `pg_restore: error:` (contenedor caído, OOM) no se toma por inocua. **Antes de
parar servicios y de `pg_restore`** (pre-vuelo) crea o comprueba cada bucket protegido con
`get-object-lock-configuration` y exige `Enabled`; si no (el bucket existía sin bloqueo y eso no se
puede habilitar a posteriori), **aborta sin haber parado ni restaurado nada**: «no se ha tocado ningún
dato» es cierto. `backup.sh` ya
no se traga un fallo al listar los buckets: exige que el espejo contenga
`orpycamcp-<slug>-documents` de cada tenant que no esté dado de baja (`active` y `suspended`); con el almacén sin responder o la red
equivocada el respaldo **falla** en vez de salir «correcto» con el espejo vacío. Ambos scripts toman
la red del servicio `minio` y usan `amazon/aws-cli` por tag y digest (`S3_CLI_IMAGEN` lo sobrescribe).

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

El manifiesto de producción (`infra/docker-compose.prod.yml`) **no construye**: cada
servicio propio arranca de `image: ${ORPYCA_REGISTRY}/<servicio>:${ORPYCA_TAG}`, y
ambas variables son **obligatorias** (`:?`; sin ellas ningún `docker compose` arranca).
El job `build-images` de GitLab publica `$CI_REGISTRY_IMAGE/<servicio>:$CI_COMMIT_SHA`
(y las escanea con Trivy antes), de modo que `ORPYCA_REGISTRY=$CI_REGISTRY_IMAGE` y
`ORPYCA_TAG=<sha>` nombran una versión que existe y a la que se puede volver. Un
guardián (`shared/tests/test_prod_inmutable.py`) impide reintroducir un `build:` o un
tag `latest` en el manifiesto.

Sin registro, se construye en el propio servidor con el override
`infra/docker-compose.build.yml` (etiqueta cada imagen con el mismo nombre, y el SHA
queda en el host para poder volver):

```bash
export ORPYCA_REGISTRY=local ORPYCA_TAG=$(git rev-parse HEAD)
docker compose -f infra/docker-compose.prod.yml -f infra/docker-compose.build.yml build
```

`infra/DOCKER_REGISTRY.md` documenta el registro de GitLab (`build-images` en
`.gitlab-ci.yml`); no hay pipeline de GitHub Actions en este repositorio.

### Migraciones al arrancar

Cada servicio con base de datos espera a su `migrate-<servicio>` (`auth`, `tenant`,
`document`, `storage`, `workflow`, `archive`, `notification`, `signature`,
`knowledge`): un contenedor de un solo uso que corre `migrations/runner.py` con la
conexión del propietario sobre la **misma imagen inmutable** que va a servir, y el
servicio solo arranca si terminó con éxito (`service_completed_successfully`). Si la
migración falla, la versión nueva no sirve contra un esquema que no es el suyo.

**El cerrojo (ADR-041).** Los nueve `migrate-*` —y `scripts/init_tenant.py`— se serializan con **un solo
cerrojo global** (`orpycamcp_common.migrations.cerrojo`, `pg_try_advisory_lock` sobre una constante fija, visible en
`pg_locks`). Quien no lo obtiene reintenta cada medio segundo hasta `MIGRATIONS_LOCK_TIMEOUT_S` (600 s por
defecto); vencido el plazo sale con un error legible («otra migración tiene el cerrojo desde hace N s (pid …)»), no
se cumple `service_completed_successfully` y **el servicio no arranca**. Hasta C19 este texto afirmaba un
`pg_advisory_lock` que el código no tenía. Las migraciones globales corren en transacción (un fallo a mitad ya no
deja DDL sin asiento en el libro). Repetir `up -d` o escalar réplicas es seguro. El paso 4 de abajo sigue siendo
válido para ejecutarlas a mano **antes** de arrancar (p. ej. para ver su salida).

**Desarrollo.** `docker-compose.yml` hace lo mismo: `docker compose up -d --build` construye las imágenes, corre los
nueve `migrate-<svc>` y solo entonces arranca cada servicio. Cada servicio lleva `image: orpycamcp/<svc>-service:dev`
y el migrador la reutiliza (`pull_policy: never`, sin `build:` propio). Para recrear **un** servicio con una imagen
nueva (la forma con `--no-deps` salta el migrador a propósito):

```bash
docker compose build <svc>-service \
  && docker compose run --rm --no-deps migrate-<svc> \
  && DB_APP_USER=orpycamcp DB_APP_PASSWORD=orpycamcp docker compose up -d --no-build --no-deps <svc>-service
```

Si el migrador falla, el `&&` no recrea el servicio. Recrear con la **misma** imagen
(`up -d --no-build --no-deps <svc>-service`) no necesita migrar: el migrador ya la aplicó.

### Procedimiento

```bash
# 1. RESPALDO VERIFICADO. No "un respaldo": uno que terminó con el sello OK.
ENV_FILE=infra/.env BACKUP_DIR=/srv/orpyca/backups ./infra/backup.sh

# 2. Anotar la versión actual — es a lo que se vuelve si algo sale mal.
docker compose -f infra/docker-compose.prod.yml images | tee /srv/orpyca/version-anterior.txt

# 3. Traer la nueva versión (sin arrancarla todavía).
export ORPYCA_REGISTRY=<registro/proyecto>   # p. ej. registry.gitlab.com/grupo/orpycamcp
export ORPYCA_TAG=<sha-del-commit>
docker compose -f infra/docker-compose.prod.yml pull

# 4. Migraciones. Cada servicio aplica las suyas y el runner recorre TODOS los
#    schemas tenant_* que encuentre, así que un tenant nuevo no se queda atrás.
#    Son idempotentes por convención (ADR-012): re-ejecutarlas es seguro.
#    OJO: es `python migrations/runner.py`, un script — NO `python -m
#    app.migrations.runner`, que es un módulo que no existe.
#    Se corren con el contenedor `migrate-<svc>` (único con la conexión del
#    PROPIETARIO): los servicios que sirven NO llevan `MIGRATIONS_DATABASE_URL`
#    en su entorno, de modo que un RCE o un volcado del entorno de un servicio
#    alcanzable por el gateway no entrega el rol que puede deshabilitar el
#    trigger de `audit_log`.
for svc in auth tenant document archive storage workflow notification signature knowledge; do
  docker compose -f infra/docker-compose.prod.yml run --rm --no-deps "migrate-${svc}"
done

# 4b. Cifrar en reposo los secretos de webhooks heredados (una vez, tras la
#    migración 009 de notification y ANTES de desplegar). Idempotente: escribe el
#    asiento `notification.webhook.secret_cifrado` desde la aplicación y deja
#    `secret` en NULL. `--verify` sale con error si queda algún secreto en claro.
#    El runtime ya no lleva la conexión del propietario: se la pasa el operador
#    solo a este contenedor de un solo uso.
docker compose -f infra/docker-compose.prod.yml run --rm --no-deps \
  -e "MIGRATIONS_DATABASE_URL=postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@postgres:5432/${DB_NAME}" \
  notification-service python -m app.ops.cifrar_webhook_secrets
docker compose -f infra/docker-compose.prod.yml run --rm --no-deps \
  -e "MIGRATIONS_DATABASE_URL=postgresql+asyncpg://${DB_USER}:${DB_PASSWORD}@postgres:5432/${DB_NAME}" \
  notification-service python -m app.ops.cifrar_webhook_secrets --verify

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
export ORPYCA_REGISTRY=<registro/proyecto>
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

Seis servicios exponen `GET /metrics` (formato de texto 0.0.4) en su **puerto propio**: `auth-service:8001`,
`archive-service:8004`, `workflow-service:8006`, `notification-service:8007`, `signature-service:8008` y
`knowledge-service:8011`. No lo exponen gateway, tenant, document, storage, mcp ni whisper. El exportador es propio
(`orpycamcp_common.metricas`, ADR-041): sin `prometheus_client`, **sin etiqueta de tenant ni de identidad** y con
medición perezosa (caché de `METRICS_TTL_SECONDS`, 30 s por defecto, plazo de 10 s).

| Familia | Servicio | Significado |
|---|---|---|
| `orpycamcp_signal_measured{signal}` | los seis | `1` = la última medición fue completa en todos los tenants; `0` = **no verificado** |
| `orpycamcp_signal_last_success_timestamp_seconds{signal}` | los seis | epoch de la última medición completa |
| `orpycamcp_dlq_pending_events` | workflow, signature, notification, knowledge | eventos pendientes en la dead-letter del grupo propio |
| `orpycamcp_dlq_oldest_pending_timestamp_seconds` | idem | el pendiente más antiguo (ausente si no hay) |
| `orpycamcp_worm_exhausted_artifacts{artifact,phase}` | archive | artefactos WORM con el reintento agotado |
| `orpycamcp_audit_chain_breaks`, `_declared_breaks`, `_verification_ok`, `_last_verification_timestamp_seconds` | auth | última corrida **completa** de `verify_audit_chains` |

**«No verificado» no es 0.** Si la medición de una señal falla en cualquier tenant, se emite
`orpycamcp_signal_measured{signal="…"} 0` y **no se emite ninguna serie de valor** de esa señal; nunca una suma
parcial. Un `orpycamcp_dlq_pending_events 0` con `signal_measured 1` sí es un cero medido. Con réplicas, cada una
reporta el mismo estado de la base: agregar con `max()`, no con `sum()`.

**Cómo se protege.** `/metrics` no lo enruta el gateway (solo reenvía `/api/v1/`), el servicio responde **404** a toda
petición con `X-Forwarded-For`, `Forwarded` o `X-Real-IP` (Prometheus no las manda; nginx y el gateway sí) y el nginx
de producción responde 404 a `/metrics` en el host de la API. En producción los servicios no publican `ports:`: Prometheus
debe unirse a `orpycamcp-net` y raspar por el nombre DNS del servicio. **Prometheus no forma parte del manifiesto
de esta versión**; si lo despliega, fije su imagen por tag **y digest** (`docker buildx imagetools inspect
<imagen>:<tag>`; nada de `:latest`) y no publique `ports:` de Prometheus ni de Grafana: acceda por un túnel o
un proxy interno autenticado, no abriendo el puerto al exterior.

```yaml
# prometheus.yml (fragmento)
scrape_configs:
  - job_name: orpycamcp
    metrics_path: /metrics
    scrape_interval: 60s
    static_configs:
      - targets:
          - auth-service:8001
          - archive-service:8004
          - workflow-service:8006
          - notification-service:8007
          - signature-service:8008
          - knowledge-service:8011
```

**La cadena de auditoría la mide el último job, no el scrape.** `orpycamcp_audit_chain_*` sale de
`public.audit_chain_runs`, donde `python -m app.ops.verify_audit_chains` anota una fila por corrida (lo invocan
`backup.sh` con `--origen backup` y `restore.sh` con `--origen restore`). Una corrida acotada con `--slug` o
`--excluir-prefijo` se anota **parcial** y no cuenta. Para que haya señal aunque no haya respaldo, programe un cron
diario (desde el anfitrión, sin montar el repo en escritura):

```bash
# /etc/cron.d/orpyca-verificar-auditoria
30 3 * * * orpyca cd /srv/orpyca && docker compose -f infra/docker-compose.prod.yml run --rm --no-deps -T \
  auth-service python -m app.ops.verify_audit_chains --anclas - --origen cron < /srv/orpyca/backups/anclas-auditoria.tsv
```

(Sin `--emitir-ancla`: el cron solo verifica y anota; anclar sigue siendo cosa de `backup.sh`.)

Alertas de ejemplo (reglas de Prometheus):

```yaml
groups:
  - name: orpycamcp
    rules:
      - alert: SenalNoVerificada
        expr: orpycamcp_signal_measured == 0
        for: 15m
      - alert: DeadLetterConEventos
        expr: max(orpycamcp_dlq_pending_events) by (job, instance) > 0
        for: 1h
      - alert: WormAgotado
        expr: sum(orpycamcp_worm_exhausted_artifacts) > 0
      - alert: CadenaDeAuditoriaRota
        expr: orpycamcp_audit_chain_breaks > 0 or orpycamcp_audit_chain_verification_ok == 0
      - alert: VerificacionDeAuditoriaVencida
        expr: time() - orpycamcp_audit_chain_last_verification_timestamp_seconds > 26 * 3600
```

En **desarrollo** `audit_chain` saldrá no verificado o con roturas: los asientos `itest-*` que dejaron las pruebas
de integración no se pueden borrar (`audit_log` solo admite anexar) y excluirlos con `--excluir-prefijo` es, por
construcción, una corrida parcial.

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

> Lo que se puede verificar en el repositorio **ya tiene guardián** y corre en cada push
> (`shared/tests/test_manifiesto_prod_seguro.py`): el manifiesto de producción no publica
> `ports:` salvo `edge` (80 y 443), Redis lleva `--requirepass ${REDIS_PASSWORD:?}`, toda credencial es obligatoria
> (`${VAR:?}`) y sin valor por defecto, y las imágenes de terceros llevan **tag y digest** (`@sha256:`; un
> tag se puede mover en el registro, el digest no). `TRUSTED_PROXIES` no incluye el gateway de la red, la
> plantilla de nginx cubre los tres dominios y `infra/.env.example` contiene toda variable del manifiesto.
> Para actualizar una imagen: `docker buildx imagetools inspect <imagen>:<tag>`, copiar el `Digest` al
> manifiesto (y a `docker-compose.yml`, para que lo validado en desarrollo sea lo que va a producción) y
> correr el guardián. **Keycloak 24.0.5 está fuera de soporte**: actualizarlo es una tanda aparte (cambia la
> API de administración y el tema).
> Lo que depende del **host** se comprueba, desde fuera del servidor, con
> `scripts/preflight-prod.sh <host-publico>` (TLS válido, HTTP redirige a HTTPS, puertos
> internos cerrados). Lo demás sigue siendo manual.

- [ ] Todos los servicios tienen TLS habilitado (`scripts/preflight-prod.sh`)
- [ ] PostgreSQL no es accesible externamente (manifiesto: guardián; host: `preflight-prod.sh`)
- [ ] El almacén de objetos requiere autenticación (sin acceso público)
- [ ] Redis requiere contraseña (manifiesto: guardián)
- [ ] Keycloak usa HTTPS con certificado válido (los tres dominios tienen certificado y `edge` los sirve; la consola `/admin/` no es pública)
- [ ] Solo `edge` publica puertos (80 y 443): Docker se salta ufw en los puertos publicados (manifiesto: guardián; host: `scripts/preflight-prod.sh`)
- [ ] `TRUSTED_PROXIES` sin el gateway de la red (`172.20.128.0/17`, no el `/16`)
- [ ] CORS configurado restringidamente
- [ ] Audit logging habilitado en PostgreSQL y Keycloak
- [ ] Backup diario con pruebas de recuperación
- [ ] Rate limiting en api-gateway activado
- [ ] Headers de seguridad (HSTS, X-Content-Type-Options, etc.)

### Cabeceras de seguridad en Nginx

Están en `infra/nginx/orpyca.conf.template` (servicio `edge`). Lo importante de esa plantilla no son las
cabeceras: son las tres cosas que dependen de `X-Forwarded-*` y fallan en silencio sin ellas — el
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

### Dictado por voz (whisper) fuera del manifiesto de producción

`whisper-service` (perfil `assistant`, arranca apagado) **no está en `infra/docker-compose.prod.yml`
en v1, y es intencional**: `mcp-server` no define `STT_PROVIDER`, así que se aplica el defecto
`disabled` y el dictado queda apagado; no falta ninguna pieza. Declararlo con `profiles: [assistant]`
costaría que Compose interpolara también los perfiles inactivos y obligara a todo operador a definir
`WHISPER_INTERNAL_TOKEN` aunque no use el dictado. Cuando una entidad lo pida, se añade el servicio
con su token **y** `STT_PROVIDER` en `mcp-server`; un guardián
(`test_stt_en_prod_tiene_quien_lo_sirva`) falla si se enciende el dictado sin el servicio.

### Auditoría de seguridad

`docker scan` está **retirado** del CLI de Docker. El pipeline escanea con
[Trivy](https://trivy.dev) cada imagen propia en el job `build-images`, **antes** de
publicarla: falla con una vulnerabilidad `CRITICAL` que tenga corrección disponible
(`--ignore-unfixed`), así que una imagen vulnerable no llega al registro. A mano, con
el tag inmutable por SHA (nunca `latest`):

```bash
trivy image --severity CRITICAL --ignore-unfixed "$ORPYCA_REGISTRY/api-gateway:$ORPYCA_TAG"
trivy image --severity CRITICAL --ignore-unfixed pgvector/pgvector:pg15
```

Las dependencias se auditan aparte (`dependency-audit`: `pip-audit` sobre cada
`requirements*.lock`, fail-closed, en el programado semanal y en cada merge request;
`npm-audit`: `npm audit --omit=dev --audit-level=high` sobre el frontend, en cada
pipeline). Ninguno de los dos se salta en silencio: un informe ausente o corrupto es
un fallo, y una vulnerabilidad con corrección disponible también.

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
