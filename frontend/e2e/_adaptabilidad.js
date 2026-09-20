// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * _adaptabilidad.js — el medidor, en UN solo sitio.
 *
 * Vive aparte de las specs porque lo usan dos carriles: el de humo (páginas
 * públicas, sin backend) y el autenticado (las pantallas de trabajo, que
 * necesitan la pila). Copiarlo sería garantizar que dentro de un mes midan
 * cosas distintas — que es justo lo que pasó con el helper de inicio de sesión
 * de este mismo directorio.
 *
 * No es un fichero `.spec`: si lo fuera, importarlo desde otra spec haría que
 * Playwright ejecutara sus tests dos veces.
 */

/**
 * Los tres anchos no son redondos por gusto: 390 es un móvil corriente, 1366 el
 * portátil más extendido —y el ancho en el que se reportó el problema— y 1920
 * el escritorio donde más espacio se desperdiciaba.
 */
export const ANCHOS = [
  { nombre: "móvil", width: 390, height: 844 },
  { nombre: "portátil", width: 1366, height: 768 },
  { nombre: "escritorio", width: 1920, height: 1080 },
];

/** Mide en el navegador lo que jsdom no puede calcular. */
export async function medir(page) {
  return page.evaluate(() => {
    const raiz = document.documentElement;
    const desbordan = [];
    for (const el of document.querySelectorAll("body *")) {
      const caja = el.getBoundingClientRect();
      if (caja.width === 0 && caja.height === 0) continue;
      // Un elemento fuera de pantalla a propósito (el enlace de salto, un
      // elemento oculto en el flanco) no cuenta: solo lo que sobresale por la
      // derecha estando visible.
      if (caja.right > window.innerWidth + 2 && getComputedStyle(el).visibility !== "hidden") {
        desbordan.push(
          `${el.tagName.toLowerCase()}.${String(el.className).split(" ")[0]} (${Math.round(caja.right)}px)`,
        );
      }
    }
    // Tablas estrujadas: las que NECESITAN más ancho del que tienen y viven en
    // un contenedor que no se desplaza.
    //
    // Ojo con cómo se mide, porque el primer intento estaba mal y el control
    // positivo lo cazó: se comparaba `scrollWidth` con el ancho del contenedor,
    // buscando un DESBORDAMIENTO que no ocurre. Una tabla no desborda: COMPRIME.
    // Ante un contenedor estrecho estrecha sus celdas hasta partir el contenido,
    // y `scrollWidth` acaba valiendo lo mismo que el contenedor — el síntoma se
    // borra a sí mismo.
    //
    // Lo que sí lo delata es el ancho NATURAL: se fuerza `width: max-content`
    // un instante, se mide lo que la tabla pediría si pudiera, y se restaura.
    const tablasRecortadas = [];
    for (const t of document.querySelectorAll("table")) {
      const cont = t.parentElement;
      if (!cont) continue;
      const seDesplaza = ["auto", "scroll"].includes(getComputedStyle(cont).overflowX);
      if (seDesplaza) continue;

      const estiloPrevio = t.style.cssText;
      t.style.width = "max-content";
      t.style.tableLayout = "auto";
      const natural = t.offsetWidth;
      t.style.cssText = estiloPrevio;

      if (natural > cont.clientWidth + 2) {
        tablasRecortadas.push(
          `${t.className || "table"} (necesita ${natural}px, tiene ${cont.clientWidth}px)`,
        );
      }
    }
    return {
      viewport: window.innerWidth,
      scrollHorizontal: raiz.scrollWidth > window.innerWidth + 2,
      scrollWidth: raiz.scrollWidth,
      desbordan: desbordan.slice(0, 5),
      tablasRecortadas,
    };
  });
}
