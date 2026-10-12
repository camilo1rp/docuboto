# ADR-033: Entrada de producción — nginx en contenedor (`edge`), `TRUSTED_PROXIES` sin el gateway de la red y terceros fijados por tag y digest

**Estado**: Aceptado
**Fecha**: 2026-10-11
**Autores**: Giampiero (mantenedor principal); triage B7
**Relacionados**: [ADR-031](ADR-031-constancia-autorizacion-ip-firmada-politica-por-entidad.md) (IP firmada en la ventanilla), [ADR-027](ADR-027-sustitucion-almacen-objetos-rustfs.md) (el tag fijo que desaparece del registro), [ADR-008](ADR-008-auditoria-inmutable.md) (`audit_log`)

---

## Contexto

La verificación en vivo de B7 mostró que el manifiesto de producción **no tenía entrada**:

1. `infra/nginx.conf.example` hacía `proxy_pass http://127.0.0.1:3000` y `:8080`, pero el compose de producción no publica ningún
   puerto (todo `expose`): con nginx en el servidor, la pila era inalcanzable (P1).
2. No había `server` para `KEYCLOAK_HOSTNAME`, y el SSR y los servicios llaman a `https://${KEYCLOAK_HOSTNAME}`: el login PKCE no
   tenía entrada pública (P2).
3. Las imágenes de terceros (`pgvector/pgvector:pg15`, `redis:7-alpine`, `keycloak:24.0`) iban por un tag que se puede mover; el
   guardián existente aceptaba «tag fijo o digest» y `pg15` lo pasaba (P3). ADR-027 ya dejó la lección: un tag fijo también puede
   desaparecer del registro.
4. `infra/.env.example` no traía `ORPYCA_REGISTRY` ni `ORPYCA_TAG`, obligatorias (`:?`) (P4).

## Decisión

### 1. nginx en contenedor, único servicio con `ports:`

Alternativa A (publicar en `127.0.0.1` y dejar nginx en el servidor): **descartada**.

- `api-gateway` y `frontend` llevan `deploy.replicas: 2`; un puerto fijo del servidor lo toma la primera réplica y la segunda
  muere con «port is already allocated».
- Rompe el guardián «ningún servicio publica puertos».
- Con nginx en el servidor, el gateway ve como par a `172.20.0.1` (docker-proxy sobre loopback), así que habría que confiar en esa
  IP (ver 2).
- Los servicios llaman a `https://${KEYCLOAK_HOSTNAME}` desde dentro de la red, lo que exige *hairpin* NAT.

Alternativa B (**elegida**): servicio `edge` (`nginx:1.30-alpine@sha256:…`), `ports: 80:80, 443:443`, plantilla
`infra/nginx/orpyca.conf.template` (la imagen sustituye `${FRONTEND_HOSTNAME}`, `${API_PUBLIC_HOSTNAME}` y `${KEYCLOAK_HOSTNAME}`),
`/etc/letsencrypt` y `certbot-www` en solo lectura, `read_only` + `tmpfs`. `proxy_pass` a una **variable** con
`resolver 127.0.0.11`: nginx resuelve en cada petición y reparte entre réplicas. El servicio lleva los tres hostnames como **alias**
de `orpycamcp-net`: el DNS de Docker resuelve `https://${KEYCLOAK_HOSTNAME}` al propio `edge`, con certificado válido y sin
*hairpin* ni DNS externo.

**P2**: `server` TLS para Keycloak que se publica por **lista de admisión, no de denegación**: solo `location /realms/orpycamcp/` y
`location /resources/` llegan a Keycloak, todo lo demás es 404 y una ruta con `;` también (la consola y el realm `master` se usan por
túnel SSH). La primera versión negaba `/admin/` y `/realms/master/`, y esa denegación se esquivaba con `/realms/master;x/…` y con
`/realms/master` sin barra final (ambas daban 200 con Keycloak real: SEG-M1). Un guardián simula la resolución de `location` del
prefijo más largo y exige que ninguna variante de `master` ni de `/admin/` alcance a Keycloak. Con búferes de cabecera ampliados (`proxy_buffer_size 128k`) para las cookies de token.

Certificados: primera emisión con `certbot certonly --standalone` **antes** del primer `up` (el puerto 80 aún está libre);
renovaciones por *webroot* con `--deploy-hook` que recarga nginx.

### 2. `TRUSTED_PROXIES` excluye el gateway de la red

nginx hace `proxy_add_x_forwarded_for`: **añade** `$remote_addr` a lo que mande el cliente. El gateway de la API recorre la cadena
desde la derecha saltando los pares de confianza. Si `$remote_addr` llegara a ser `172.20.0.1` (un cliente IPv6 servido por el
docker-proxy, o un proceso del servidor) y esa IP fuera de confianza, el gateway la saltaría y se quedaría con la entrada **forjada
por el cliente**, que acaba sellada en `audit_log` y en la constancia de la ventanilla (ADR-031).

La red pasa a `ipam: subnet 172.20.0.0/16, ip_range 172.20.128.0/17, gateway 172.20.0.1` y el valor por defecto de
`TRUSTED_PROXIES` es `172.20.128.0/17`. Con el gateway de la red **fuera** del rango de confianza el resultado es *fail-closed*: se
registra `172.20.0.1`, nunca una IP inventada. El SSR (`XFF_DEPTH=1`) lee la entrada de la derecha, que es el `$remote_addr` real
con DNAT IPv4. Para conservar la IP de un cliente IPv6 hace falta `"ip6tables": true` en el demonio de Docker. Un `.env` heredado
con `TRUSTED_PROXIES=172.20.0.0/16` reabre el defecto: se documenta, un guardián vigila el valor por defecto y **el gateway de la API no
arranca en producción** (`APP_ENV=production`) si `TRUSTED_PROXIES` cubre la puerta de enlace de la red, que toma de
`GATEWAY_RED_DOCKER_GATEWAY` o de la ruta por defecto del contenedor (ARC-B4; en desarrollo el valor `172.16.0.0/12` la contiene a
propósito y no se comprueba).

`/health/ready` (que enumera el estado de los servicios) solo admite `127.0.0.1`: se retiró `allow 172.20.0.1`, porque esa es
justamente la IP con que nginx ve a cualquier cliente que llegue por el docker-proxy (SEG-B1). Se sondea con
`docker exec <edge> wget --no-check-certificate -qO- --header 'Host: <API_PUBLIC_HOSTNAME>' https://127.0.0.1/health/ready`.

### 3. Terceros por tag **y** digest

`postgres`, `redis`, `keycloak`, `edge` y `amazon/aws-cli` (en `backup.sh`/`restore.sh`, antes `:latest`) se fijan a un tag legible
**más** `@sha256:` (comprobado contra el registro; los digests de desarrollo coinciden con los tags). Un tag se puede mover; el
digest no. El guardián exige `@sha256:` y otro recorre `infra/*.sh` buscando `:latest`. Se alinean las mismas fijaciones en
`docker-compose.yml` para que lo validado en desarrollo sea lo que va a producción. Keycloak 24.0.5 está fuera de soporte:
actualizarlo cambia la API de administración y el tema, y es otra tanda.

### 4. `.env.example` cubre el manifiesto; whisper fuera de producción

Un guardián exige que toda `${VAR}` del compose de producción aparezca en `infra/.env.example` (activa o comentada) y que las `:?`
aparezcan **activas**. `whisper-service` (perfil `assistant`) **no entra** en el manifiesto de producción en v1: `mcp-server` no
define `STT_PROVIDER`, el defecto es `disabled` y no falta ninguna pieza; declararlo obligaría a todo operador a definir
`WHISPER_INTERNAL_TOKEN` (Compose interpola también los perfiles inactivos) o a meterlo en `DEFECTO_DECLARADO`, una excepción a la
regla de credenciales. Un guardián falla si se enciende el dictado sin el servicio.

## Consecuencias

- **Operación**: hay que emitir tres certificados (uno por dominio) antes del primer `up`; las renovaciones pasan a *webroot*. La
  consola de Keycloak ya no es pública (lista de admisión). `infra/nginx.conf.example` desaparece (lo sustituye la plantilla).
- **Seguridad**: el único puerto publicado es 80/443 y Docker se salta ufw en los puertos publicados, así que *no publicar* es la
  defensa; la IP sellada en `audit_log` no se puede forjar por la cabecera aunque el cliente llegue con la IP del gateway.
- **Pruebas**: lista de admisión exacta de `ports` (`{"edge": {"80:80","443:443"}}`), `TRUSTED_PROXIES` sin el gateway, plantilla
  con los tres hosts y sin `proxy_pass` a loopback, alias de `edge`, digests, `.env.example` y STT, con una mutación por guardián.
- **No verificado en este entorno**: `nginx -t` real (la imagen no se descargó: disco crítico, y no hay nginx en el anfitrión) ni `edge`
  levantado; la plantilla se valida con `docker compose config` y los guardianes. Los `listen [::]:80/443` están escritos (los tres
  `server` TLS y el de redirección) y un guardián cuenta que no falte ninguno, pero sin `nginx -t` tampoco se sabe si el anfitrión
  admite IPv6 en el socket. Debe correrse `exec edge nginx -t` en el primer despliegue.
- **Deuda declarada**: Keycloak 24.0.5 sin soporte; `get_tenant_conn` en auth-service sigue dando 500 con un tenant sin schema en los
  demás endpoints, y un tenant `suspended` con el schema intacto sigue resolviendo permisos en `/auth/me` (decidir si el estado del
  registro de tenants debe pesar ahí).
