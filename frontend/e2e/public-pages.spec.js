// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * public-pages.spec.js — Páginas públicas que deben renderizar/responder
 * sin backend (Keycloak/gateway no están arriba en este smoke).
 *
 * /login NO renderiza su marcado "Redirigiendo…" en un flujo real: su
 * `load()` (+page.server.js) SIEMPRE hace `redirect(302, ...)` a Keycloak
 * antes de que SvelteKit envíe HTML — ese marcado transitorio solo es
 * visible en condiciones de carrera del navegador y no es reproducible de
 * forma determinista en E2E. Por eso aquí se verifica el contrato real y
 * determinista: el 302 hacia el Authorization Endpoint de Keycloak con los
 * parámetros PKCE correctos. El render del marcado "Redirigiendo…" en sí se
 * cubre a nivel de componente en Vitest (no requiere red).
 */
import { test, expect } from "@playwright/test";

test.describe("Páginas públicas", () => {
  test("/login redirige (302) al Authorization Endpoint de Keycloak con PKCE", async ({
    request,
  }) => {
    const res = await request.get("/login", { maxRedirects: 0 });
    expect(res.status()).toBe(302);

    const location = res.headers()["location"];
    expect(location).toBeTruthy();
    const authUrl = new URL(location);

    expect(authUrl.pathname).toContain("/protocol/openid-connect/auth");
    expect(authUrl.searchParams.get("client_id")).toBe("orpycamcp-frontend");
    expect(authUrl.searchParams.get("response_type")).toBe("code");
    expect(authUrl.searchParams.get("code_challenge_method")).toBe("S256");

    // La aplicación DECLARA su lengua de trabajo (OIDC Core §3.1.2.1). Sin este
    // parámetro Keycloak negocia con el `Accept-Language` del navegador, y un
    // funcionario con el navegador en inglés veía la pantalla de acceso en
    // inglés seguida de una aplicación enteramente en español. Se comprueba
    // aquí, en el contrato del redirect, porque es un parámetro que se pierde
    // en un refactor sin que nada se rompa a la vista.
    expect(authUrl.searchParams.get("ui_locales")).toBe("es");
    expect(authUrl.searchParams.get("code_challenge")).toBeTruthy();
    expect(authUrl.searchParams.get("state")).toBeTruthy();
    expect(authUrl.searchParams.get("redirect_uri")).toContain("/login/callback");
  });

  test("/login fija cookies httpOnly de PKCE (verifier/state) — nunca accesibles a document.cookie", async ({
    request,
  }) => {
    const res = await request.get("/login", { maxRedirects: 0 });
    const setCookie = res.headers()["set-cookie"] ?? "";
    expect(setCookie.toLowerCase()).toContain("httponly");
  });

  test("/403 renderiza la página de acceso denegado", async ({ page }) => {
    await page.goto("/403");
    await expect(page.getByRole("heading", { name: "Acceso denegado" })).toBeVisible();
    // El <a> lleva role="button" explícito en el marcado (no un <a> plano).
    await expect(page.getByRole("button", { name: "Volver al panel principal" })).toBeVisible();
  });
});
