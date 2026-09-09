// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
// @vitest-environment node

/**
 * Una frase no puede vivir dentro de un contenedor `display: flex`.
 *
 * El defecto real (revisión por navegador, B12, `/admin/pinar/[id]`): el aviso
 * que advierte de que aprobar un plan PINAR **congela su contenido de forma
 * permanente** estaba en un `<p class="pinar-workspace__hint">` con
 * `display: flex`. Cada hijo directo pasa a ser una columna, así que el icono,
 * los tres trozos de texto y los dos `<strong>` se repartieron en SEIS columnas
 * y la advertencia quedó ilegible.
 *
 * El idioma correcto ya existía en el código (`disp-danger__text`,
 * `cert-drawer__legal-banner`): envolver la prosa en un `<span>` para que el
 * flex tenga dos hijos —icono y texto— en vez de seis. PINAR lo olvidó.
 *
 * Ni `svelte-check` ni `eslint` ni `prettier` ven esto: el marcado es válido y
 * el CSS también. Solo se ve mirando la pantalla, o con este censo.
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { execSync } from "node:child_process";

/** Etiquetas que indican que el elemento NO es un banner de prosa sino un contenedor. */
const HIJOS_DE_BLOQUE = /<(div|section|ul|ol|table|header|footer|form|nav)\b|<[A-Z]|\{#(if|each)/;
/** Énfasis en línea: lo que se convierte en columna dentro de un flex. */
const EN_LINEA = /<(strong|em|b|code)\b/;

function archivosSvelte() {
  return execSync("find src -name '*.svelte'", { encoding: "utf-8" }).trim().split("\n");
}

/**
 * Clases con `display: flex` declaradas en el propio archivo (incluye `&__x`).
 *
 * Lee las declaraciones PROPIAS de cada selector: desde su `{` hasta el primer
 * `}` **o** el primer `{` anidado, lo que llegue antes. La primera versión de
 * este censo usaba `[^{}]*` y por eso **toda regla con un selector anidado le
 * era invisible** — se le escaparon los tres avisos de `pinar-workspace__warning`,
 * que son justo las advertencias de los tres actos irreversibles del plan. Un
 * censo con un punto ciego es peor que no tenerlo: aprueba en silencio.
 *
 * **Segundo defecto, en la otra dirección (2026-08-24).** El censo comparaba
 * solo el SUFIJO BEM de la clase (`.split("__").pop()`), así que dos bloques
 * distintos con el mismo elemento colisionaban: `.correo__aviso`, que es flex,
 * marcaba como culpable a `.form__aviso`, que no lo es. Un falso positivo es
 * menos grave que un falso negativo, pero enseña a desconfiar del guardián — y
 * un guardián del que se desconfía se acaba silenciando. Ahora se resuelve el
 * `&` de Sass contra el bloque que lo envuelve y se compara el nombre completo.
 */
function clasesFlex(estilos) {
  const encontradas = new Set();
  // Pila de bloques abiertos, para resolver el `&` de Sass: dentro de
  // `.correo { &__aviso { … } }` el nombre real de la clase es `correo__aviso`.
  const pila = [];
  const re = /([&.][A-Za-z0-9_-]+)[^{}]*\{|\{|\}/g;
  let m;
  while ((m = re.exec(estilos))) {
    if (m[0] === "}") {
      pila.pop();
      continue;
    }
    if (!m[1]) {
      // Una llave que no abre una regla de clase (media query, selector de
      // elemento). Se apila igual para que el `}` que le corresponde no
      // desequilibre la pila.
      pila.push("");
      continue;
    }
    const padre = pila.length ? pila[pila.length - 1] : "";
    const nombre = m[1].startsWith("&") ? padre + m[1].slice(1) : m[1].slice(1);
    pila.push(nombre);

    const cierre = estilos.indexOf("}", re.lastIndex);
    const anidado = estilos.indexOf("{", re.lastIndex);
    const hasta = anidado !== -1 && anidado < cierre ? anidado : cierre;
    const propias = estilos.slice(re.lastIndex, hasta === -1 ? undefined : hasta);
    if (/display:\s*flex/.test(propias)) encontradas.add(nombre);
  }
  return encontradas;
}

/**
 * Texto que es hijo DIRECTO del elemento (profundidad 0).
 *
 * Es la medida que importa: en un contenedor flex, cada hijo directo se
 * convierte en una columna. El texto que vive dentro de un `<span>` es UN solo
 * hijo y se compone como una frase normal — que es exactamente la corrección.
 * Contar «todo el texto sin etiquetas» no distingue el caso roto del arreglado.
 */
function textoDirecto(cuerpo) {
  const sinComentarios = cuerpo.replace(/<!--[\s\S]*?-->/g, "");
  let profundidad = 0;
  let suelto = "";
  const partes = sinComentarios.split(/(<[^>]+>)/);
  for (const parte of partes) {
    if (parte.startsWith("<")) {
      const autoCierre = parte.endsWith("/>") || /^<(i|br|img|input|hr)\b/.test(parte);
      if (parte.startsWith("</")) profundidad = Math.max(0, profundidad - 1);
      else if (!autoCierre) profundidad += 1;
      continue;
    }
    if (profundidad === 0) suelto += " " + parte;
  }
  return suelto.split(/\s+/).filter(Boolean).length;
}

/**
 * Contenido de un elemento, contando la anidación de su propia etiqueta.
 *
 * **Tercer punto ciego del censo, y el más caro (2026-08-24).** Antes el cuerpo
 * se extraía con `([\s\S]*?)<\/\1>` no codicioso, que cierra en el PRIMER
 * `</div>` que aparezca — el de un hijo, no el suyo. Con un `<div>` contenedor
 * flex envolviendo la pantalla, la primera coincidencia se comía todo hasta ese
 * cierre y `lastIndex` saltaba **por detrás de los contenedores interiores**,
 * que quedaban sin examinar. La pantalla de correo tiene esa forma, y el censo
 * daba verde sobre un defecto reproducido a propósito. Contar la profundidad
 * cuesta unas líneas y elimina la clase entera de fallo.
 *
 * @param {string} markup
 * @param {number} inicio índice justo después del `>` de la etiqueta de apertura
 * @param {string} etiqueta
 */
function cuerpoBalanceado(markup, inicio, etiqueta) {
  const re = new RegExp(`<${etiqueta}\\b[^>]*>|</${etiqueta}\\s*>`, "g");
  re.lastIndex = inicio;
  let profundidad = 1;
  let m;
  while ((m = re.exec(markup))) {
    profundidad += m[0].startsWith("</") ? -1 : 1;
    if (profundidad === 0) return markup.slice(inicio, m.index);
  }
  return markup.slice(inicio);
}

/**
 * Devuelve los banners de prosa que quedarían troceados en columnas.
 * @param {string} texto contenido de un `.svelte`
 */
export function prosaEnFlex(texto) {
  const [markup, ...resto] = texto.split("<style");
  const flex = clasesFlex(resto.join("<style"));
  if (flex.size === 0) return [];

  const hallazgos = [];
  for (const m of markup.matchAll(/<(p|div|li)\s([^>]*)>/g)) {
    const clases = /class="([^"]+)"/.exec(m[2])?.[1];
    if (!clases) continue;
    const cuerpo = cuerpoBalanceado(markup, m.index + m[0].length, m[1]);
    const casa = clases.split(/\s+/).some((c) => flex.has(c));
    if (!casa) continue;
    if (!EN_LINEA.test(cuerpo)) continue;
    // Un contenedor de bloques/componentes no es un banner de prosa: el análisis
    // aplana anidamientos y sin esto sale un aluvión de falsos positivos.
    if (HIJOS_DE_BLOQUE.test(cuerpo)) continue;
    const palabrasSueltas = textoDirecto(cuerpo);
    if (palabrasSueltas < 8) continue;
    hallazgos.push({ clases, palabrasSueltas });
  }
  return hallazgos;
}

describe("prosa dentro de un contenedor flex", () => {
  it("detecta el defecto original de PINAR (control positivo)", () => {
    // Sin este control, un extractor roto devolvería [] y el censo aprobaría
    // por no haber mirado nada.
    const defectuoso = `
      <p class="x__hint">
        <i class="fas fa-circle-info"></i>
        Mientras el plan esté en <strong>borrador</strong> puede completarse su contenido.
        Al <strong>aprobarlo</strong>, el contenido queda congelado de forma permanente.
      </p>
      <style lang="scss">.x { &__hint { display: flex; gap: 4px; } }</style>`;
    expect(prosaEnFlex(defectuoso)).toHaveLength(1);
  });

  it("no marca la prosa ya envuelta en un <span>", () => {
    const correcto = `
      <p class="x__hint">
        <i class="fas fa-circle-info"></i>
        <span>Mientras el plan esté en <strong>borrador</strong> puede completarse su contenido
        y al <strong>aprobarlo</strong> queda congelado.</span>
      </p>
      <style lang="scss">.x { &__hint { display: flex; } }</style>`;
    expect(prosaEnFlex(correcto)).toHaveLength(0);
  });

  it("ninguna pantalla trocea una frase en columnas", () => {
    const archivos = archivosSvelte();
    // Anti-vacuidad: si `find` falla, el censo debe caer en rojo, no aprobar.
    expect(archivos.length).toBeGreaterThan(50);

    const culpables = [];
    for (const ruta of archivos) {
      for (const h of prosaEnFlex(readFileSync(ruta, "utf-8"))) {
        culpables.push(`${ruta} (class=${h.clases}, ${h.palabrasSueltas} palabras sueltas)`);
      }
    }
    expect(culpables).toEqual([]);
  });
});
