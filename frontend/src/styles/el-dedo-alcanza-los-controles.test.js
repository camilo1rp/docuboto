// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * el-dedo-alcanza-los-controles.test.js — el mínimo táctil vive en la hoja
 * global, no repetido componente a componente.
 *
 * El criterio (WCAG 2.5.5, 44×44 CSS px) ya existía, y se aplicaba a mano: 19
 * declaraciones de `@media (pointer: coarse)` en diez archivos. Un censo de los
 * `<button>` nativos del árbol encontró **62 controles sin ningún mínimo
 * declarado**. Repararlos uno a uno es exactamente cómo se llegó a 62 — el
 * mismo error que ya se pagó con el estilo de `code`, corregido tres veces
 * pantalla por pantalla antes de mirar la hoja global.
 *
 * Qué mide este guardián: que la regla global EXISTA y diga lo que tiene que
 * decir. No prohíbe que un componente declare lo suyo —conviven, y las de
 * `Button` son más específicas con el mismo valor—; prohíbe que la red de
 * seguridad desaparezca sin que nadie se entere.
 *
 * Lo que NO puede medir, y se dice en voz alta: jsdom no calcula layout, así que
 * ningún test de esta suite sabe cuántos píxeles ocupa un botón renderizado.
 * Esto es un guardián sobre el CSS, no una medición del resultado. El recuento
 * de 62 se hizo con un censo aparte sobre el árbol.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const AQUI = dirname(fileURLToPath(import.meta.url));
const UTILITIES = join(resolve(AQUI), "utilities.scss");

/**
 * El bloque `@media (pointer: coarse)` de la hoja global, con su cuerpo.
 *
 * Se busca con la llave de apertura pegada, y no el texto suelto: la primera
 * versión de esto encontraba la MENCIÓN dentro del comentario que hay justo
 * encima de la regla, y el guardián acababa midiendo su propia prosa. Es el
 * mismo tropiezo que ya se anotó una vez en esta suite — un comentario que se
 * aplica a sí mismo.
 */
function bloqueTactilGlobal(css) {
  const inicio = css.indexOf("@media (pointer: coarse) {");
  if (inicio === -1) return null;
  // Cierre por conteo de llaves: el bloque anida el selector dentro del media.
  let profundidad = 0;
  for (let i = css.indexOf("{", inicio); i < css.length; i += 1) {
    if (css[i] === "{") profundidad += 1;
    else if (css[i] === "}") {
      profundidad -= 1;
      if (profundidad === 0) return css.slice(inicio, i + 1);
    }
  }
  return null;
}

describe("objetivo táctil — la red de seguridad global", () => {
  const css = readFileSync(UTILITIES, "utf-8");
  const bloque = bloqueTactilGlobal(css);

  it("utilities.scss declara el mínimo táctil bajo `pointer: coarse`", () => {
    expect(
      bloque,
      "Sin esta regla, los 62 controles que no declaran tamaño propio vuelven a " +
        "quedar por debajo del objetivo táctil, y el defecto es invisible en " +
        "escritorio — que es donde se desarrolla.",
    ).toBeTruthy();
  });

  it("cubre el `<button>` nativo, que es donde estaba el hueco", () => {
    // El censo encontró el problema en `<button>` sin clase de componente:
    // pestañas de /admin, cierres de modal y cajón, paginación de DataTable.
    expect(bloque).toMatch(/(^|[\s,{])button\s*[,{]/m);
    expect(bloque).toContain('[role="button"]');
  });

  it("usa `min-height`/`min-width` y no `height`: un control mayor no encoge", () => {
    expect(bloque).toMatch(/min-height:\s*44px/);
    expect(bloque).toMatch(/min-width:\s*44px/);
    // `height: 44px` obligaría a los botones grandes a encoger — la regla es un
    // suelo, no una talla única.
    expect(bloque).not.toMatch(/[^-]height:\s*\d+px/);
  });

  it("NO toca casillas ni radios: ahí lo que se agranda es el label", () => {
    // Forzar 44px sobre un checkbox pinta una casilla enorme en vez de una zona
    // cómoda de pulsar. Es una exclusión deliberada, y por eso se fija.
    expect(bloque).not.toContain('input[type="checkbox"]');
    expect(bloque).not.toContain('input[type="radio"]');
  });

  it("solo aplica a puntero grueso: el escritorio no cambia", () => {
    // Control positivo del alcance. Sin el media query, la regla engordaría
    // todos los botones con ratón y cambiaría la densidad de 48 pantallas.
    const fuera = css.replace(bloque, "");
    expect(fuera).not.toMatch(/min-height:\s*44px/);
  });
});
