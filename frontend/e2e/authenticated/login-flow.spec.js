// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * login-flow.spec.js — Login PKCE completo contra Keycloak real.
 *
 * Corre SOLO con `--config=playwright.authenticated.config.js`, que es el que
 * apunta a este directorio; el config de humo lo sigue excluyendo por
 * `testIgnore` para no depender de la pila. La única barrera que queda es la
 * legítima: sin `E2E_USERNAME`/`E2E_PASSWORD` el caso que necesita identidad
 * se salta. El `describe.skip` que había encima se retira — un test apagado
 * en su propio archivo no se enciende nunca, y este llevaba así desde que se
 * escribió.
 */
import { test, expect } from "@playwright/test";
import { hayCredenciales, iniciarSesion } from "./_sesion.js";

// Esta suite PRUEBA el inicio de sesión, así que arranca sin sesión: no hereda
// el estado que el proyecto de preparación deja para el resto.
test.use({ storageState: { cookies: [], origins: [] } });

test.describe("Login PKCE (requiere la pila levantada — ver README.md)", () => {
  test("un usuario válido completa el login y llega a /dashboard con sesión", async ({ page }) => {
    test.skip(!hayCredenciales(), "Faltan E2E_USERNAME/E2E_PASSWORD");

    await iniciarSesion(page);

    await expect(page).toHaveURL(/\/dashboard/);
    // La cookie de sesión es httpOnly: no debe ser legible desde document.cookie.
    const clientCookies = await page.evaluate(() => document.cookie);
    expect(clientCookies).not.toContain("access_token");
  });

  test("credenciales inválidas NO fijan sesión y no dejan pasar a /dashboard", async ({ page }) => {
    // No necesita credenciales: precisamente prueba las que no valen.
    await page.goto("/login");
    await page.fill("#username", "usuario-invalido");
    await page.fill("#password", "clave-incorrecta");
    await page.click("#kc-login");

    // Keycloak reintenta en SU propia página, no en `/login` de la aplicación:
    // comprobar `toHaveURL(/\/login/)` daba un falso verde porque la ruta de
    // Keycloak contiene «login-actions». Lo que importa es que NO se entra.
    await expect(page.locator("#kc-login")).toBeVisible();
    await expect(page).not.toHaveURL(/\/dashboard/);

    // Y que no queda sesión: la guarda SSR devuelve al login.
    await page.goto("/dashboard");
    await expect(page).not.toHaveURL(/\/dashboard$/);
  });
});
