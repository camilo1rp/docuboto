# Realms de Keycloak

Dos archivos, y el nombre de cada uno dice para qué entorno sirve.

> **El JSON de un realm NO admite claves de comentario.** Keycloak lo deserializa
> contra `RealmRepresentation` con Jackson y **cualquier** propiedad desconocida
> —incluida una que empiece por `_` y que solo pretendía documentar— aborta la
> importación con `UnrecognizedPropertyException`. El fallo no es cosmético: el
> contenedor **no arranca**, y como el servidor de identidad es la puerta de
> entrada, todo el sistema queda inaccesible. Por eso la prosa vive aquí y no
> dentro de los `.json`; `shared/tests/test_realm_keycloak_importable.py` lo
> vigila.

## `realm-dev.json` — desarrollo

Es el que monta `docker-compose.yml` (`--import-realm`). Trae usuarios con
contraseña publicada en el repositorio (`orpycamcp_dev`), secretos de cliente
`change-me-in-production`, `sslRequired: none` y `redirectUris` a localhost.
**No se despliega**: el nombre anterior (`realm-export.json`) no decía a qué
entorno pertenecía y eso era precisamente el riesgo.

### Duración de las sesiones (`ssoSessionIdleTimeout` / `ssoSessionMaxLifespan`)

SOLO DESARROLLO. Keycloak trae 30 min de inactividad y 5 min de *access token*;
en una demostración o en una sesión de pruebas larga eso echa a la persona a
mitad de camino y parece una avería. Se sube la **inactividad** a 8 h y se deja
el *access token* en su valor por defecto: la renovación transparente de
`hooks.server.js` lo repone sola, así que alargarlo no aporta nada y sí
ampliaría la ventana de un token robado. En producción **no** se copia este
bloque.

## `realm-prod.json.example` — plantilla de producción

Cópiala a `realm-prod.json`, sustituye **todo** lo que aparece entre `<>` y
móntala en Keycloak (ver `infra/docker-compose.prod.yml`, servicio `keycloak`,
sección `volumes` comentada).

Los campos `<...>` no tienen valor por defecto: si se importa sin sustituirlos,
Keycloak falla. Es deliberado — un default funcional aquí es una puerta abierta.

Este archivo **no crea usuarios**. Las cuentas de producción se dan de alta desde
la consola de administración o se federan contra el directorio de la entidad; no
se versionan en el repositorio.
