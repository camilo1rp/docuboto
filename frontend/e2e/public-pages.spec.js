// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Smoke de páginas públicas, sin backend (la app no está arriba en este smoke).
 * /login es un formulario propio (ADR-048): se comprueba que se pinta en este
 * mismo origen. La acción que envía las credenciales se prueba en Vitest.
 */
import { test, expect } from "@playwright/test";

test.describe("Páginas públicas", () => {
  test("/login muestra el formulario propio (ADR-048), sin salir a otro origen", async ({
    page,
  }) => {
    const res = await page.goto("/login");
    expect(res?.status()).toBe(200);
    await expect(page.getByLabel("Usuario")).toBeVisible();
    await expect(page.getByLabel("Contraseña")).toHaveAttribute("type", "password");
    await expect(page.getByRole("button", { name: "Ingresar" })).toBeVisible();
    expect(new URL(page.url()).pathname).toBe("/login");
  });

  test("/403 renderiza la página de acceso denegado", async ({ page }) => {
    await page.goto("/403");
    await expect(page.getByRole("heading", { name: "Acceso denegado" })).toBeVisible();
    // El <a> lleva role="button" explícito en el marcado (no un <a> plano).
    await expect(page.getByRole("button", { name: "Volver al panel principal" })).toBeVisible();
  });
});
