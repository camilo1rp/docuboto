# ADR-048: La identidad la emite Docuboto, no Keycloak

**Estado**: Aceptado
**Fecha**: 2026-11-07
**Autores**: Camilo Romero
**Relacionados**: [ADR-013](ADR-013-autorizacion-resuelta-en-bd.md) (la BD autoriza; **se conserva**), [ADR-016](ADR-016-firma-electronica.md) (firma electrónica), [ADR-024](ADR-024-root-fuera-de-banda.md) (ROOT fuera de banda), [ADR-035](ADR-035-estado-de-la-entidad-cumplimiento-en-el-borde.md) (veto de la entidad; **se reubica**), [ADR-045](ADR-045-pasarela-de-inteligencia-y-protocolo-ppi.md) (PPI; su autenticación cambia), [ADR-047](ADR-047-un-proceso-y-cuatro-contenedores.md), [ADR-051](ADR-051-ppi-reducido-vive-en-la-app.md)

---

## Contexto

Keycloak cuesta un contenedor con JVM y 1 GB de límite, tres clientes (`api-gateway` con ROPC, `auth-service` bearer-only, `orpycamcp-frontend` con PKCE), un realm JSON que hay que editar a mano con secretos, y un realm que **en producción no se importa**: el montaje del volumen está comentado (`infra/docker-compose.prod.yml:296-305`) y el comando es `start` sin `--import-realm`.

De esos tres clientes, dos están muertos: `keycloak_client_id` / `keycloak_client_secret` no los lee ningún código, y el secreto del cliente bearer-only nunca se usa. `KEYCLOAK_GATEWAY_CLIENT_SECRET` no está en ningún manifiesto, así que cae al valor por defecto `"change-me-in-production"`.

Lo que Keycloak aporta de verdad es corto:

- **Autenticación de personas**: Authorization Code + PKCE para el frontend y ROPC para un cliente programático.
- **Política de contraseñas y bloqueo por intentos**: 12 caracteres, historial de 5, bloqueo por fuerza bruta. Todo eso vive en el realm, no en el código.
- **Emisión y rotación de tokens**: access 300 s, refresh 1800 s (o el absoluto de 36000 s).
- **JWKS**: una única función lo consume (`auth/core/security.py:55`) y lo cachea 600 s en memoria del proceso.

Y lo que **no** aporta:

- **La autorización** la resuelve PostgreSQL en cada request, sin caché (`shared/orpycamcp_common/rbac.py`). El propio código lo dice: *«Keycloak autentica; la BD del tenant autoriza»*.
- **El segundo factor** vive entero en `auth-service` (RFC 6238, `auth/core/totp.py`), en una tabla del esquema de la entidad, cifrado con `TOTP_ENCRYPTION_KEY`. Keycloak no tiene política OTP configurada en ningún realm. Y no participa del inicio de sesión: es un **paso de escalada para firmar**, que consume `signature-service` por `/internal/totp/*`.
- **Las identidades**: `auth_users.id` es `gen_random_uuid()`. El único rastro de Keycloak es `auth_users.keycloak_sub`, una columna con `UNIQUE`, y `rg keycloak_sub` la confina a `services/auth-service/**`. Ninguna tabla de ningún esquema referencia esa columna.

Al mismo tiempo, **PPI sí depende de Keycloak**: el token de servicio que knowledge-service presenta al proveedor sale de `grant_type=client_credentials` contra el realm (`knowledge/core/token_ppi.py:95-104`), y proboto lo valida contra un `issuer` + `jwks_uri` y exige un principal de tipo servicio.

En síntesis: un contenedor de 1 GB sostiene un factor de autenticación, una política de contraseñas y un emisor de tokens para clientes máquina. Es más barato emitirlo que administrarlo, sobre todo cuando la pieza que PPI necesita (un emisor OIDC con JWKS y credenciales de cliente) es la que el propio proceso puede publicar.

## Decisión

### 1. Un proveedor de identidad en el proceso

`auth` publica tres superficies y ninguna depende de un servicio externo:

| Superficie | Ruta | Uso |
|---|---|---|
| Inicio de sesión | `POST /api/v1/auth/login`, `/refresh`, `/logout` | Personas, con contraseña argon2id |
| Descubrimiento OIDC | `GET /.well-known/openid-configuration` | Consumidores máquina (proboto) |
| Claves públicas | `GET /.well-known/jwks.json` | Verificación de los JWT emitidos |
| Credenciales de cliente | `POST /oauth/token` con `grant_type=client_credentials` | Cuentas de servicio por entidad |

- **Contraseñas**: `argon2id` vía `argon2-cffi`. Se retira la política del realm; se implementa la misma en `auth` (longitud mínima, historial con `argon2` de las últimas N, y bloqueo temporal escribiendo `failed_logins` y `locked_until`, columnas que **ya existen** en `auth_users` y que hoy no escribe nadie).
- **Sesiones**: JWT firmados con `Ed25519` (o `ES256`) usando la dependencia `cryptography` que ya está en el árbol para firma. Access 15 min, refresh 12 h con rotación y detección de reuso. Los refresh se guardan hasheados en una tabla del esquema de la entidad, no en cookie con estado implícito.
- **Claves**: pares de firma en una tabla con `kid` y versionado; la clave pública se publica en JWKS. Rotación sin cortar: se publican dos claves, se firma con la nueva y se sigue aceptando la anterior hasta que expire el último token.
- **Cuentas de servicio**: `auth_service_accounts(client_id, secret_hash, tenant_slug, audience, scopes)` por entidad. El token que emite lleva `iss`, `aud`, `sub` y un claim de tipo de principal, para que un consumidor pueda distinguir persona de servicio.
- **El frontend** deja de hablar con Keycloak: `login/+page.server.js` pasa a un formulario propio contra `POST /auth/login`; `auth.server.js` llama a la app en vez de al endpoint del realm. Se conservan los nombres de cookie (`op_access_token`, `op_refresh_token`) y el comportamiento de sesión vencida: cookies limpias y `redirect(302, /login?next=…)`.
- **La sesión no incluye TOTP**, igual que hoy: TOTP sigue siendo escalada para firmar, no factor de login. Cambiar eso sería una decisión distinta y este ADR no la toma.

### 2. Lo que se retira

| Pieza | Destino |
|---|---|
| Contenedor `keycloak` y su volumen | Fuera del manifiesto |
| `infra/keycloak/realm-prod.json.example`, `realm-dev.json`, `scripts/init_tenant.py` §admin-API (399–600) | Se borran |
| `auth/core/security.py` (JWKS remoto, validación con `iss`/`typ`/`azp`) | Se sustituye por verificación con la clave local |
| `keycloak_client_id`, `keycloak_client_secret`, `KEYCLOAK_AUTH_SERVICE_SECRET`, `KEYCLOAK_API_GATEWAY_SECRET`, `KEYCLOAK_GATEWAY_CLIENT_SECRET`, `allowed_azp`, `keycloak_url`, `keycloak_realm`, `keycloak_issuer` | Se borran |
| `auth_users.keycloak_sub`, `ldap_username` | Se borran (no hay datos que migrar) |
| ROPC y PKCE contra el realm | Se sustituyen por `/auth/login` |
| `POST /auth/validate` y la caché MAC del gateway | Se retira con el gateway (ADR-047); la validación es local |
| `intelligence-{slug}` como cliente de Keycloak | Pasa a cuenta de servicio en la tabla propia (ADR-051) |
| `superadmin` leído de `realm_access.roles` | Pasa a `auth_users.is_root` / un rol en la BD, que es donde ya vive el resto de la autorización |

### 3. PPI se autentica contra este emisor

El proveedor recibe `iss = https://{API_PUBLIC_HOSTNAME}`, `jwks_uri = https://{API_PUBLIC_HOSTNAME}/.well-known/jwks.json` y un `audience` declarado en la conexión. Del lado del proveedor, `app.idp_links` ya modela `issuer` + `audience` + `jwks_uri` arbitrarios, y `app.service_accounts` se indexa por `(issuer, client_id)`: no hay cambio de contrato, solo de URL de emisor y de un `client_id` que ya no crea Keycloak.

El callback del proveedor hacia Docuboto (`POST /ppi/v1/{slug}/content`) **no cambia**: su HMAC por entidad es independiente del emisor de identidad.

### 4. El veto de la entidad se reubica

ADR-035 vetaba en el borde, en el gateway. Con el gateway retirado, el veto pasa a dos puntos y solo dos:

- el `POST /auth/login` (una entidad no operativa no emite tokens de personas), y
- el middleware de request (una entidad que se suspende con sesiones vivas deja de servir el API autenticado en el siguiente request).

Lo que ADR-035 garantiza —que una entidad suspendida conserva lectura pública y preservación— no cambia.

## Alternativas consideradas

- **Mantener Keycloak y solo quitar los dos clientes muertos.** Descartado: el ahorro real (1 GB, un contenedor, un realm que hay que editar a mano y que en producción ni se importa) está en retirarlo, no en podarlo. Los dos clientes muertos se retiran igual.
- **Un IdP externo (Authentik, Zitadel, Ory).** Descartado por el mismo motivo y con un costo mayor: otro contenedor, otra base, otra consola y otra copia de respaldo que hoy no existe.
- **OIDC contra GOV.CO / cuenta institucional.** Descartado por ahora: no todas las alcaldías categoría 4–6 tienen identidad federada, y hacerlo obligatorio encierra el producto en un proveedor. Se deja la puerta abierta con el mismo patrón de `idp_links` que usa el consumidor: si una entidad tiene OIDC corporativo, se declara como emisor adicional y se enlaza `sub` a `auth_users.id` — no cambia el resto.
- **Contraseñas con `passlib`.** Descartado: `passlib` está sin mantenimiento desde 2020; `argon2-cffi` es la biblioteca mantenida y es una dependencia directa.
- **Emitir tokens con `PyJWT` y clave simétrica.** Descartado: HS256 obligaría a distribuir el secreto a todo el que verifique, incluido un proveedor externo. Ed25519 publica una clave pública y no comparte secreto.

## Consecuencias

- **Un contenedor y 1 GB menos.** Y con ello se van el realm JSON, los secretos de cliente y el paso de "editar el realm a mano" del alta de una entidad.
- **La política de contraseñas pasa a ser código con pruebas.** Antes la aplicaba Keycloak y no había nada que probar en este repositorio; ahora `auth` tiene que probar longitud, historial y bloqueo, y esas pruebas son parte del entregable.
- **Se gana control sobre el ciclo de vida del usuario.** El alta de una persona deja de ser «crear en Keycloak y esperar el primer login»: se crea en `auth_users` y el `sub` es propio.
- **Se pierde todo lo que la consola de Keycloak daba gratis**: recuperación de contraseña por correo, verificación de correo, sesiones administrables, políticas por grupo. Recuperación de contraseña se implementa (con `notification`); las demás quedan declaradas como no disponibles.
- **La rotación de claves de firma es responsabilidad propia.** Se implementa con `kid` y ventana de solapamiento; sin ella, rotar rompe todas las sesiones y el token de servicio del proveedor.
- **Un token de servicio comprometido sigue siendo una credencial de entidad.** No empeora ni mejora: el alcance lo fija `scopes` en la fila de la cuenta.
- **Migración de datos**: nula. No hay usuarios que migrar; `keycloak_sub` se borra.

## Preguntas abiertas

1. **Segundo factor en el inicio de sesión.** Hoy TOTP no participa del login. Una alcaldía que quiera exigirlo a todos los funcionarios necesita una decisión aparte: activarlo en el login cambiaría el flujo que este ADR conserva. Se deja como está y se declara.
2. **Recuperación de contraseña.** Es la única función de Keycloak que se pierde y que el usuario nota. Debe entrar antes del primer cliente real, con token de un solo uso por correo.
3. **Verificar que proboto acepta el emisor propio.** `PrincipalKind.SERVICE` y `app.idp_links` lo permiten, pero hay que probarlo contra el proveedor antes de declararlo.
