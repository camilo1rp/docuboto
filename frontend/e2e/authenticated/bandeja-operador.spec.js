// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * bandeja-operador.spec.js — Bandeja + acción de operador con backend real (SKIP).
 *
 * Requiere `docker compose up -d` y un usuario con `PERM_RADI` y al menos un
 * radicado en su bandeja. Ver e2e/authenticated/README.md.
 */
import { test, expect } from "@playwright/test";

test.describe.skip("Bandeja — vista consolidada de operador (requiere stack real)", () => {
  test.beforeEach(async ({ page }) => {
    const username = process.env.E2E_USERNAME;
    const password = process.env.E2E_PASSWORD;
    test.skip(!username || !password, "Faltan E2E_USERNAME/E2E_PASSWORD");

    await page.goto("/login");
    await page.getByLabel(/usuario|username/i).fill(username);
    await page.getByLabel(/contraseña|password/i).fill(password);
    await page.getByRole("button", { name: /iniciar sesión|sign in/i }).click();
    await expect(page).toHaveURL(/\/dashboard$/);
  });

  test("la bandeja lista radicados y permite abrir el detalle sin cambiar de pestaña", async ({
    page,
  }) => {
    await page.goto("/bandeja");
    await expect(page.getByRole("table")).toBeVisible();

    const firstRow = page.getByRole("row").nth(1);
    await firstRow.click();

    // Vista consolidada (ADR-020 §2): el detalle con acciones de operador
    // aparece en la MISMA vista (drawer/panel), no en una pestaña nueva.
    await expect(page.getByRole("dialog")).toBeVisible();
  });

  test("una acción de escritura (p. ej. anular) exige confirmación explícita", async ({ page }) => {
    await page.goto("/bandeja");
    await page.getByRole("row").nth(1).click();
    await page.getByRole("button", { name: /anular/i }).click();

    await expect(
      page.getByRole("dialog", { name: /confirmar/i }).or(page.getByText(/¿confirmas/i)),
    ).toBeVisible();
  });
});
