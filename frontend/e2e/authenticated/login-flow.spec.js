// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * login-flow.spec.js — Login PKCE completo contra Keycloak real (SKIP).
 *
 * Requiere `docker compose up -d` (Keycloak + api-gateway + Postgres) y un
 * usuario de prueba. Ver e2e/authenticated/README.md para cómo habilitarlo.
 * Doble barrera para no correr por accidente: `test.describe.skip` +
 * `playwright.config.js#testIgnore`.
 */
import { test, expect } from "@playwright/test";

test.describe.skip("Login PKCE (requiere stack real — ver README.md)", () => {
  test("un usuario válido completa el login y llega a /dashboard con sesión", async ({ page }) => {
    const username = process.env.E2E_USERNAME;
    const password = process.env.E2E_PASSWORD;
    test.skip(!username || !password, "Faltan E2E_USERNAME/E2E_PASSWORD");

    await page.goto("/login");
    // Keycloak real: formulario de login del tema por defecto.
    await page.getByLabel(/usuario|username/i).fill(username);
    await page.getByLabel(/contraseña|password/i).fill(password);
    await page.getByRole("button", { name: /iniciar sesión|sign in/i }).click();

    await expect(page).toHaveURL(/\/dashboard$/);
    // La cookie de sesión es httpOnly: no debe ser legible desde document.cookie.
    const clientCookies = await page.evaluate(() => document.cookie);
    expect(clientCookies).not.toContain("access_token");
  });

  test("credenciales inválidas vuelven a /login con error, sin fijar sesión", async ({ page }) => {
    await page.goto("/login");
    await page.getByLabel(/usuario|username/i).fill("usuario-invalido");
    await page.getByLabel(/contraseña|password/i).fill("clave-incorrecta");
    await page.getByRole("button", { name: /iniciar sesión|sign in/i }).click();

    await expect(page).toHaveURL(/\/login/);
  });
});
