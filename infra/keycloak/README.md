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

## Tema de las pantallas de identidad (`themes/orpyca/`)

La pantalla de acceso es la **única del recorrido que no pasa por SvelteKit**, así que se quedó
fuera de todo el trabajo de diseño: salía con el tema por defecto —fondo poligonal gris, botón
azul PatternFly, «Sign in to your account» en inglés— justo **antes** de la pantalla de Orpyca,
que sí está cuidada. Van seguidas, así que el contraste era máximo.

El tema hereda de `keycloak` (`parent=keycloak`) y **no copia ninguna plantilla `.ftl`**: solo
aporta tokens y superficies. Es deliberado — una plantilla copiada se queda congelada en la
versión en que se copió y hay que re-auditarla en cada actualización, que es justo donde viven
los arreglos de seguridad de las pantallas de identidad.

### El idioma: dos mitades, y `defaultLocale` es solo una

`internationalizationEnabled` + `defaultLocale: es` en el realm **no bastan**: `defaultLocale` es
el respaldo para cuando el navegador no expresa preferencia, y siempre la expresa. Un funcionario
con el navegador en inglés seguía viendo la pantalla de acceso en inglés seguida de una
aplicación enteramente en español.

La otra mitad la pone el frontend: `ui_locales=es` en la petición de autorización (OIDC Core
§3.1.2.1), es decir, **la aplicación declara en qué lengua trabaja**. Está cubierto por un test
de contrato en `frontend/e2e/public-pages.spec.js`, porque es un parámetro que se pierde en un
refactor sin que nada se rompa a la vista. El selector de idioma del tema sigue disponible para
quien necesite otra.

### ⚠️ Cambiar `realm-dev.json` NO afecta a un despliegue existente

`start-dev --import-realm` importa **solo si el realm no existe**. Sobre una instalación ya
arrancada, editar el JSON no cambia nada: hay que aplicarlo por la API de administración. Es el
modo de fallo clásico de «cambié la configuración y el sistema siguió igual».

```bash
TOK=$(curl -s -X POST http://localhost:19180/realms/master/protocol/openid-connect/token \
  -d grant_type=password -d client_id=admin-cli -d username=admin -d password=<clave> \
  | python3 -c "import json,sys;print(json.load(sys.stdin)['access_token'])")

curl -X PUT -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" \
  -d '{"realm":"orpycamcp","loginTheme":"orpyca","internationalizationEnabled":true,
       "supportedLocales":["es","en"],"defaultLocale":"es"}' \
  http://localhost:19180/admin/realms/orpycamcp
```

El JSON queda actualizado igualmente: es lo que recibe una instalación nueva.
