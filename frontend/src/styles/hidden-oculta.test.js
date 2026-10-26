// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
// @vitest-environment node

/**
 * El atributo `hidden` tiene que ocultar.
 *
 * Lo oculta la hoja del navegador con `display: none` y especificidad de
 * elemento, así que cualquier `display: flex` de una clase gana la cascada. En
 * `/busqueda`, `/reportes` y `/admin/preservacion` los paneles de las pestañas
 * llevaban `display: flex` y se pintaban los tres a la vez: la navegación por
 * pestañas era decorativa, y un lector de pantalla anunciaba una pestaña
 * seleccionada con el contenido de todas presente.
 *
 * Este test no comprueba una pantalla: comprueba que la regla global existe y
 * que gana. Es lo que hace que la próxima pestaña nazca funcionando.
 */

import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { join, resolve } from "node:path";

const SRC = resolve(process.cwd(), "src");
const UTILIDADES = join(SRC, "styles", "utilities.scss");

describe("[hidden] oculta", () => {
  it("la regla global existe y es !important", () => {
    const css = readFileSync(UTILIDADES, "utf8");
    const regla = /\[hidden\]\s*\{[^}]*display:\s*none\s*!important/;
    expect(regla.test(css)).toBe(true);
  });

  it("y entra en la aplicación por el punto de entrada de estilos", () => {
    // Una regla en un archivo que nadie importa no oculta nada.
    const app = readFileSync(join(SRC, "styles", "app.scss"), "utf8");
    expect(app).toMatch(/@use\s+"utilities"/);
  });

  it("y la regla es la ÚLTIMA palabra: nada la redefine después", () => {
    // `utilities` se importa el último en `app.scss` a propósito. Si un día
    // pasara antes que otra hoja que redeclare `display` sobre estos paneles,
    // la regla volvería a perder la cascada — y el defecto sería idéntico.
    const app = readFileSync(join(SRC, "styles", "app.scss"), "utf8");
    const usos = [...app.matchAll(/@use\s+"([a-z-]+)"/g)].map((m) => m[1]);
    expect(usos[usos.length - 1]).toBe("utilities");
  });
});
