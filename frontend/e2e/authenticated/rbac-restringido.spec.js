// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * rbac-restringido.spec.js — La interfaz espeja el RBAC con DOS perfiles
 * (AREA-12-07). Requiere la pila levantada y `seed_demo.py` ejecutado.
 *
 * `consulta` (PERM_RADI + USUA_PERM_EXPEDIENTE, nivel 1, sin USUA_PERM_ADMIN)
 * frente a `e2e2` (el grupo «Demostración», con USUA_PERM_ADMIN):
 *  - el menú es DISTINTO: solo quien administra ve «Administración»;
 *  - forzar `/admin/usuarios` sin el permiso termina en `/403` (el layout de
 *    `admin/` redirige; el backend sigue re-validando cada llamada);
 *  - quien sí tiene el permiso entra.
 */
import { test, expect } from "@playwright/test";

import { IDENTIDADES, RESTRINGIDA, estadoDe } from "./_sesion.js";

test.describe("perfil restringido (consulta)", () => {
  test.use({ storageState: estadoDe(RESTRINGIDA) });

  test("el menú no ofrece Administración", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(page.getByRole("navigation").first()).toBeVisible();
    await expect(page.getByRole("link", { name: /^administración$/i })).toHaveCount(0);
  });

  test("forzar /admin/usuarios termina en /403", async ({ page }) => {
    await page.goto("/admin/usuarios");
    await expect(page).toHaveURL(/\/403$/);
    await expect(page.getByText(/acceso denegado/i).first()).toBeVisible();
  });

  test("forzar un área con OTRO permiso (/admin/trd) también termina en /403", async ({ page }) => {
    await page.goto("/admin/trd");
    await expect(page).toHaveURL(/\/403$/);
  });
});

test.describe("perfil con USUA_PERM_ADMIN (contraste)", () => {
  test.use({ storageState: estadoDe(IDENTIDADES[1]) });

  test("ve Administración y entra a /admin/usuarios", async ({ page }) => {
    await page.goto("/dashboard");
    await expect(page.getByRole("link", { name: /^administración$/i }).first()).toBeVisible();
    await page.goto("/admin/usuarios");
    await expect(page).toHaveURL(/\/admin\/usuarios/);
  });
});
