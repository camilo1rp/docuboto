// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * route-guard.spec.js — Guarda de sesión SSR de (app)/+layout.server.js.
 *
 * Prueba que el layout de (app) redirige (302) ANTES de renderizar el árbol
 * de componentes cuando no hay sesión. Se usa `request` (APIRequestContext)
 * con `maxRedirects: 0` en vez de `page.goto` para inspeccionar el PRIMER
 * salto de la redirección sin que el navegador intente seguir la cadena
 * hasta Keycloak/el gateway (que no están arriba en este smoke sin backend)
 * — eso evitaría un net::ERR_CONNECTION_REFUSED no relacionado con lo que
 * queremos probar.
 *
 * NOTA sobre D-16 (bug histórico: 500 SSR por un `onDestroy`/bloque reactivo
 * que tocaba `document` sin guardar): el redirect 302 de este layout corta
 * ANTES de montar el árbol de componentes, así que este suite NO ejercita ese
 * patrón (no hay onMount/onDestroy que corra aquí). La regresión de D-16 se
 * cubre con una compilación SSR real (`generate: "ssr"`, sin jsdom) de
 * Modal.svelte/Drawer.svelte en
 * `src/lib/components/ui/ssrSafety.test.js` — jsdom no sirve para esto porque
 * en jsdom `document` siempre existe, ocultando exactamente el bug que
 * causó D-16.
 */
import { test, expect } from "@playwright/test";

test.describe("Guarda de rutas protegidas — sin cookies de sesión", () => {
  test("/bandeja sin sesión redirige (302) a /login?next=... antes de tocar el gateway", async ({
    request,
  }) => {
    const res = await request.get("/bandeja", { maxRedirects: 0 });
    expect(res.status()).toBe(302);
    const location = res.headers()["location"];
    expect(location).toBeTruthy();
    expect(location).toMatch(/^\/login\?next=/);
    expect(decodeURIComponent(location.split("next=")[1])).toBe("/bandeja");
  });

  test("/dashboard sin sesión también redirige a /login?next=/dashboard", async ({ request }) => {
    const res = await request.get("/dashboard", { maxRedirects: 0 });
    expect(res.status()).toBe(302);
    expect(res.headers()["location"]).toBe("/login?next=%2Fdashboard");
  });

  test("/expedientes sin sesión redirige a /login (protege TODO (app)/, no solo bandeja)", async ({
    request,
  }) => {
    const res = await request.get("/expedientes", { maxRedirects: 0 });
    expect(res.status()).toBe(302);
    expect(res.headers()["location"]).toMatch(/^\/login\?next=%2Fexpedientes/);
  });
});
