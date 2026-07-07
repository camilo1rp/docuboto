// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * playwright.config.js — Suite E2E de humo (smoke) de OrpycaMCP frontend.
 *
 * Corre contra el BUILD/PREVIEW del front (adapter-node), SIN backend real
 * (gateway/Keycloak no están arriba). Por eso el smoke se limita a lo que es
 * determinista sin servicios externos:
 *   - guardas de ruta SSR ((app)/+layout.server.js redirige antes de tocar
 *     el gateway si no hay cookie de sesión)
 *   - páginas públicas que no dependen de datos del gateway (/403)
 *   - el redirect PKCE de /login hacia Keycloak (se verifica el 302 y sus
 *     parámetros, sin navegar realmente al host de Keycloak — que no está
 *     arriba en este smoke).
 *
 * El proyecto "authenticated" (login PKCE completo + vistas con datos reales)
 * requiere Keycloak + gateway + Postgres vía `docker compose up -d` y NO se
 * ejecuta en este smoke — ver e2e/authenticated/README.md.
 */
import { defineConfig, devices } from "@playwright/test";

const PORT = 4173;
const baseURL = `http://127.0.0.1:${PORT}`;

export default defineConfig({
  testDir: "./e2e",
  // El suite "authenticated" vive en su propio directorio y se excluye del
  // run por defecto (requiere la stack completa — ver PIEZA 3 del contrato).
  testIgnore: ["**/authenticated/**"],
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"]],
  use: {
    baseURL,
    trace: "on-first-retry",
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
  ],
  webServer: {
    // Usa el build de producción real (adapter-node) — más fiel que `dev`.
    command: "npm run build && npm run preview",
    // La app no define una ruta "/" (todo vive bajo (app)/ y (public)/) ni
    // sirve index.html estático, así que "/" y "/index.html" devuelven 404 —
    // Playwright trata un 404 en la URL de arranque como "servidor no listo"
    // y nunca deja de esperar. Se usa /403 (página pública real, 200) como
    // señal de que el servidor Node (adapter-node) ya está sirviendo.
    url: `${baseURL}/403`,
    reuseExistingServer: !process.env.CI,
    timeout: 180_000,
  },
});
