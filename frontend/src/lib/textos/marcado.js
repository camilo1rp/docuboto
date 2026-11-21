// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * marcado.js — Extrae de un `.svelte` el texto que puede llegar a la pantalla.
 *
 * Lo usan los guardianes de texto del ADR-057 §10. Deja fuera lo que no se ve:
 * `<script>`, `<style>`, comentarios, expresiones `{…}` y los atributos que son
 * maquinaria (`class`, `href`, `bind:`, `on:`…). Conserva el texto entre
 * etiquetas y los atributos que se leen (`label`, `hint`, `placeholder`,
 * `title`, `aria-label`…).
 *
 * Los saltos de línea se conservan para que cada hallazgo apunte a su línea.
 */

/** Atributos que no se ven en pantalla. */
const ATRIBUTOS_MAQUINARIA = new Set([
  "class",
  "id",
  "href",
  "src",
  "name",
  "for",
  "type",
  "role",
  "style",
  "method",
  "action",
  "accept",
  "autocomplete",
  "inputmode",
  "pattern",
  "rel",
  "target",
  "lang",
  "tabindex",
  "value",
  "key",
  "size",
  "variant",
  "icon",
  "color",
  "kind",
  "mode",
  "slot",
  "form",
  "enctype",
  "width",
  "height",
  "viewbox",
  "d",
  "fill",
  "stroke",
  "xmlns",
  "download",
  "aria-controls",
  "aria-describedby",
  "aria-labelledby",
  "aria-hidden",
  "aria-live",
  "aria-current",
  "aria-expanded",
  "aria-invalid",
  "aria-modal",
  "testid",
  "endpoint",
  "permiso",
  "permission",
]);

/** Sustituye un tramo por espacios y conserva sus saltos de línea. */
function blanquear(tramo) {
  return tramo.replace(/[^\n]/g, " ");
}

/** Quita las expresiones `{…}`, respetando llaves y comillas anidadas. */
function sinExpresiones(texto) {
  let salida = "";
  let i = 0;
  while (i < texto.length) {
    if (texto[i] !== "{") {
      salida += texto[i++];
      continue;
    }
    let profundidad = 0;
    let comilla = null;
    const inicio = i;
    for (; i < texto.length; i++) {
      const c = texto[i];
      if (comilla) {
        if (c === "\\") i++;
        else if (c === comilla) comilla = null;
        continue;
      }
      if (c === '"' || c === "'" || c === "`") comilla = c;
      else if (c === "{") profundidad++;
      else if (c === "}" && --profundidad === 0) {
        i++;
        break;
      }
    }
    // Queda como `"   "` para que `attr={…}` siga siendo un atributo con valor
    // vacío y no se trague al siguiente.
    const tramo = texto.slice(inicio, i);
    salida += tramo.length >= 2 ? `"${blanquear(tramo.slice(1, -1))}"` : blanquear(tramo);
  }
  return salida;
}

/** Deja en cada etiqueta solo los valores de los atributos que se leen. */
function soloAtributosVisibles(texto) {
  return texto.replace(
    /<\/?[A-Za-z][\w:.-]*((?:\s+[^\s=>/]+(?:\s*=\s*(?:"[^"]*"|'[^']*'|[^\s>]+))?)*)\s*\/?>/g,
    (etiqueta, atributos) => {
      let visibles = "";
      for (const m of atributos.matchAll(/([^\s=>/]+)(?:\s*=\s*("[^"]*"|'[^']*'|[^\s>]+))?/g)) {
        const nombre = m[1].toLowerCase();
        const valor = m[2];
        if (!valor) continue;
        if (ATRIBUTOS_MAQUINARIA.has(nombre) || nombre.includes(":") || nombre.startsWith("data-"))
          continue;
        visibles += " " + valor.replace(/^["']|["']$/g, "");
      }
      // Se conserva la cantidad de líneas de la etiqueta original.
      const saltos = (etiqueta.match(/\n/g) ?? []).length;
      return " " + visibles.replace(/\n/g, " ") + "\n".repeat(saltos) + " ";
    },
  );
}

/**
 * Texto visible de un componente, línea por línea.
 *
 * @param {string} fuente Contenido del `.svelte`.
 * @returns {string[]} Una entrada por línea del archivo original.
 */
export function textoVisible(fuente) {
  let t = fuente
    .replace(/<script\b[\s\S]*?<\/script>/g, blanquear)
    .replace(/<style\b[\s\S]*?<\/style>/g, blanquear)
    .replace(/<!--[\s\S]*?-->/g, blanquear);
  t = sinExpresiones(t);
  t = soloAtributosVisibles(t);
  return t.split("\n");
}

/**
 * Hallazgos de un conjunto de expresiones sobre el texto visible.
 *
 * @param {string} fuente
 * @param {RegExp[]} patrones
 * @returns {{ linea: number, texto: string, coincide: string }[]}
 */
export function hallazgos(fuente, patrones) {
  const encontrados = [];
  textoVisible(fuente).forEach((linea, i) => {
    for (const patron of patrones) {
      const m = linea.match(patron);
      if (m) encontrados.push({ linea: i + 1, texto: linea.trim(), coincide: m[0] });
    }
  });
  return encontrados;
}

/**
 * Expresión que reconoce una palabra entera del español, sin importar
 * mayúsculas. `\b` no sirve con tildes, así que se delimita a mano.
 *
 * @param {string[]} palabras
 */
export function palabrasEnteras(palabras) {
  const alternativas = palabras.map((p) => p.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|");
  return new RegExp(`(?<![\\p{L}\\p{N}_-])(?:${alternativas})(?![\\p{L}\\p{N}_-])`, "iu");
}
