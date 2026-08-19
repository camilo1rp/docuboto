// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * playwright.authenticated.config.js — la suite que SÍ necesita la pila entera.
 *
 * El README de `e2e/authenticated/` llevaba tiempo diciendo «crea un segundo
 * config sin esa exclusión». Este es. Sin él, la única forma de correr esos
 * tests era editar a mano el `testIgnore` del config de humo, y un test que
 * exige editar su propia configuración para ejecutarse no se ejecuta nunca.
 *
 * Diferencias con `playwright.config.js` (el smoke):
 *
 *  - **No levanta un `webServer`.** El front que se prueba es el del `docker
 *    compose` (`orpycamcp-frontend`), porque lo que se ejerce aquí es el
 *    sistema completo: Keycloak emite el token, el gateway enruta y Postgres
 *    responde. Un preview aislado no serviría.
 *  - **No excluye `authenticated/`** — es justo lo que corre.
 *  - **Sin paralelismo entre archivos.** Estos tests ejecutan actos reales
 *    contra un tenant real; dos que escriban a la vez se pisan.
 *
 * Uso:
 *
 *   docker compose --profile dev --profile assistant up -d
 *   E2E_USERNAME=jefe E2E_PASSWORD='...' \
 *     npx playwright test --config=playwright.authenticated.config.js
 *
 * Las credenciales NO tienen valor por defecto a propósito: un default
 * funcional aquí acabaría commiteado y sería una cuenta conocida más.
 */
import { defineConfig, devices } from "@playwright/test";

const baseURL = process.env.E2E_BASE_URL ?? "http://localhost:19300";

export default defineConfig({
  testDir: "./e2e/authenticated",
  fullyParallel: false,
  workers: 1,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: [["list"]],
  timeout: 60_000,
  use: {
    baseURL,
    trace: "retain-on-failure",
    // Los actos de esta suite dejan asiento en `audit_log`: si un test falla,
    // el vídeo y la traza son la única forma de saber en qué paso ocurrió.
    video: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
