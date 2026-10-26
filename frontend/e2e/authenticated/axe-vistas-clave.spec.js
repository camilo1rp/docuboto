// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * axe-vistas-clave.spec.js — Accesibilidad automática en las vistas clave
 * (AREA-12-07). Requiere la pila levantada y `seed_demo.py` ejecutado.
 *
 * axe-core recorre cada pantalla con un navegador real y FALLA si encuentra una
 * violación `serious` o `critical` (contraste, nombres accesibles, ARIA rota,
 * etiquetas ausentes). Las `minor`/`moderate` se informan en el mensaje del
 * fallo de otras, pero no tumban la suite: un guardián que rechaza todo se
 * apaga. Sustituye al «a11y» que hasta hoy era una revisión manual.
 *
 * El nivel es WCAG 2.1 AA (`wcag2a`, `wcag2aa`, `wcag21a`, `wcag21aa`), el
 * mismo que declara el proyecto.
 */
import AxeBuilder from "@axe-core/playwright";
import { test, expect } from "@playwright/test";

import { IDENTIDADES, estadoDe } from "./_sesion.js";

const VISTAS = [
  { ruta: "/dashboard", nombre: "tablero" },
  { ruta: "/bandeja?vista=lista", nombre: "bandeja" },
  { ruta: "/radicar", nombre: "radicar" },
  { ruta: "/expedientes", nombre: "expedientes" },
  { ruta: "/firmas", nombre: "firmas" },
  { ruta: "/admin", nombre: "administración" },
  { ruta: "/perfil", nombre: "perfil" },
];

const GRAVES = new Set(["serious", "critical"]);

test.use({ storageState: estadoDe(IDENTIDADES[2]) });

for (const { ruta, nombre } of VISTAS) {
  test(`${nombre} no tiene violaciones graves de accesibilidad`, async ({ page }) => {
    await page.goto(ruta);
    await page.waitForLoadState("networkidle");

    const { violations } = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "wcag21a", "wcag21aa"])
      .analyze();

    const graves = violations.filter((v) => GRAVES.has(v.impact ?? ""));
    expect(
      graves.map((v) => `${v.impact} ${v.id}: ${v.help} (${v.nodes.length} nodos)`),
      `${ruta}: axe encontró violaciones graves`,
    ).toEqual([]);
  });
}
