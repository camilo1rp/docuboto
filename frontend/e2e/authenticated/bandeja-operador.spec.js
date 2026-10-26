// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
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

import { IDENTIDADES, estadoDe } from "./_sesion.js";

// La sesión la abre `_auth.setup.js` una sola vez para toda la suite: estos dos
// tests solo LEEN la bandeja, así que no necesitan una sesión recién abierta.
test.use({ storageState: estadoDe(IDENTIDADES[1]) });

test.describe("Bandeja — vista consolidada de operador (requiere la pila levantada)", () => {
  test("la vista Lista muestra radicados y el detalle abre en la MISMA vista", async ({ page }) => {
    // `?vista=lista` se resuelve en SSR: la bandeja abre por defecto en
    // Tablero, no en una tabla. La primera versión de este test daba por
    // supuesta una tabla y esperaba 60 s a una fila que nunca iba a existir.
    await page.goto("/bandeja?vista=lista");

    const tabla = page.getByRole("table");
    await expect(tabla).toBeVisible();
    // Al menos un radicado: si el seed no sembró nada, el test debe caer aquí
    // y no más adelante por un motivo que despista.
    await expect(page.getByRole("row").nth(1)).toBeVisible();

    // El nombre accesible NO es el texto visible «Detalle»: el botón lleva
    // `aria-label="Ver detalle de radicado {nº}"`, que lo sustituye — buena
    // práctica de accesibilidad, y el motivo de que un localizador por el
    // texto visible no encontrara nada.
    await page
      .getByRole("button", { name: /ver detalle de radicado/i })
      .first()
      .click();

    // Vista consolidada (ADR-020 §2): el detalle es un cajón en la MISMA
    // pantalla, no una pestaña ni una navegación.
    await expect(page.getByRole("dialog")).toBeVisible();
    await expect(page).toHaveURL(/\/bandeja/);
  });

  test("sobre una Entrada NO se ofrece anular — es un acto que sobre ese tipo no existe", async ({
    page,
  }) => {
    // Regla por TIPO documental, ortogonal al RBAC: un documento de Entrada
    // entró, y eso no se deja sin efecto. El backend responde 409
    // `acto_no_aplica_al_tipo` —no 403—, y la interfaz no debe ofrecer el acto.
    // Se comprueba la AUSENCIA, que es segura: el test no ejecuta nada
    // irreversible sobre el tenant.
    await page.goto("/bandeja?vista=lista&tipo=E");

    await expect(page.getByRole("row").nth(1)).toBeVisible();
    await page
      .getByRole("button", { name: /ver detalle de radicado/i })
      .first()
      .click();

    const cajon = page.getByRole("dialog");
    await expect(cajon).toBeVisible();

    // ANTI-VACUIDAD: una ausencia también se cumple si el cajón se abrió vacío
    // o si las acciones no llegaron a pintarse. Se exige primero que la zona de
    // acciones EXISTA —«Responder» está siempre en una Entrada— para que el
    // `toHaveCount(0)` de abajo signifique «no se ofrece» y no «no había nada».
    await expect(cajon.getByRole("button", { name: /responder/i })).toBeVisible();

    await expect(cajon.getByRole("button", { name: /anular/i })).toHaveCount(0);
  });
});
