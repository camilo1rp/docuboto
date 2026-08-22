// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

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
 */
function clasesFlex(estilos) {
  const encontradas = new Set();
  for (const m of estilos.matchAll(/([&.][A-Za-z0-9_-]+)\s*\{/g)) {
    const desde = m.index + m[0].length;
    const cierre = estilos.indexOf("}", desde);
    const anidado = estilos.indexOf("{", desde);
    const hasta = anidado !== -1 && anidado < cierre ? anidado : cierre;
    const propias = estilos.slice(desde, hasta === -1 ? undefined : hasta);
    if (/display:\s*flex/.test(propias)) {
      encontradas.add(m[1].replace(/^[&.]/, "").split("__").pop());
    }
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
 * Devuelve los banners de prosa que quedarían troceados en columnas.
 * @param {string} texto contenido de un `.svelte`
 */
export function prosaEnFlex(texto) {
  const [markup, ...resto] = texto.split("<style");
  const flex = clasesFlex(resto.join("<style"));
  if (flex.size === 0) return [];

  const hallazgos = [];
  for (const m of markup.matchAll(/<(p|div|li)\s[^>]*class="([^"]+)"[^>]*>([\s\S]*?)<\/\1>/g)) {
    const [, , clases, cuerpo] = m;
    const casa = clases.split(/\s+/).some((c) => flex.has(c.split("__").pop()));
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
