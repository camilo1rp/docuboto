# Suite "authenticated" — requiere la stack completa

Los tests de este directorio ejercitan el login PKCE **real** contra Keycloak y
vistas autenticadas (bandeja, acciones de operador) contra el `api-gateway`
real. **No corren en el smoke por defecto**: `playwright.config.js` los
excluye vía `testIgnore: ["**/authenticated/**"]`, y además cada archivo usa
`test.describe.skip` como segunda barrera explícita.

## Cómo correrlos

1. Levantar la stack completa desde la raíz del repo:

   ```bash
   docker compose up -d
   ```

   Espera a que Keycloak, el `api-gateway` y Postgres estén healthy (ver
   `docker compose ps`). Debe existir al menos un usuario de prueba en el
   realm `orpycamcp` con permisos de radicación (`PERM_RADI`).

2. Quitar el `.skip` de `test.describe.skip(...)` en los archivos de este
   directorio (o correrlos directamente apuntando el `testIgnore` a otra
   cosa) y exportar credenciales de prueba, p. ej.:

   ```bash
   export E2E_USERNAME=operador.qa
   export E2E_PASSWORD='********'
   ```

3. Ejecutar explícitamente este directorio (fuera del `testIgnore` global):

   ```bash
   npx playwright test e2e/authenticated --config=playwright.config.js
   ```

   (edita temporalmente `testIgnore` en `playwright.config.js` o crea un
   segundo config `playwright.authenticated.config.js` sin esa exclusión,
   con `webServer` apuntando a variables de entorno reales de
   `PUBLIC_API_URL`/`PUBLIC_KEYCLOAK_URL` en vez de los defaults locales).

## Por qué no corre en CI/smoke por defecto

- Requiere Docker Compose completo (Postgres + Keycloak + 11 microservicios)
  arriba y con datos semilla — infraestructura pesada para un chequeo de PR.
- Depende de credenciales de un usuario de prueba (secreto), no solo de código.
- Es inherentemente más flaky que el smoke sin backend (arranque de
  contenedores, timing de Keycloak).

El smoke sin backend (`e2e/route-guard.spec.js`, `e2e/public-pages.spec.js`)
cubre de forma determinista el contrato de la guarda de sesión SSR y las
páginas públicas, que es donde vivió el bug histórico D-16 (500 SSR sin
pruebas de navegador).
