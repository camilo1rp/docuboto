// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * una-tabla-ancha-se-desplaza.test.js — toda `<table>` vive dentro de un
 * contenedor que se desplaza.
 *
 * Lo reportó el usuario: «algunas pantallas muestran tablas recortadas». Y el
 * modo de fallo no es el que parece: una `<table width: 100%>` con muchas
 * columnas **no desborda, comprime**. Las celdas se estrechan hasta que el
 * contenido se parte por la mitad, y lo que queda es una tabla ilegible sin un
 * solo píxel de desbordamiento que delate el problema. Por eso no se ve como un
 * defecto de layout: se ve como una tabla fea.
 *
 * El censo encontró ocho tablas así —tres de 17 columnas en `/admin/eliminacion`,
 * dos en `/admin/trd`, dos en el tablero de PINAR y una en la consulta pública
 * de eliminación—.
 *
 * El guardián exige además el trío de accesibilidad, porque un contenedor que se
 * desplaza y no se puede recorrer con el teclado cambia un problema por otro
 * (WCAG 2.1.1, técnica SCR29): `tabindex="0"` para alcanzarlo sin ratón,
 * `role="region"` para que sea una parada de navegación y `aria-label` para que
 * el lector de pantalla diga de qué tabla se trata.
 */
import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const AQUI = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(AQUI, "..");

/**
 * Clases que envuelven una tabla en una región desplazable. `DataTable` trae la
 * suya desde antes que existiera la utilidad global y ya cumple el contrato
 * entero, así que cuenta: el guardián vigila el comportamiento, no el nombre.
 */
const ENVOLTORIOS =
  /class="[^"]*(?:op-table-scroll|table-wrap|tabla-scroll|op-datatable__wrapper)[^"]*"/;

/** Un `<table>` mencionado dentro de un comentario no es una tabla. */
function sinComentarios(txt) {
  return txt.replace(/<!--[\s\S]*?-->/g, "");
}

function svelte(dir) {
  /** @type {string[]} */
  const salida = [];
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const ruta = join(dir, e.name);
    if (e.isDirectory()) salida.push(...svelte(ruta));
    else if (e.isFile() && ruta.endsWith(".svelte")) salida.push(ruta);
  }
  return salida;
}

/** Tablas del archivo y envoltorios desplazables que contiene. */
function contar(txt) {
  const limpio = sinComentarios(txt);
  const tablas = (limpio.match(/<table\b/g) ?? []).length;
  const envoltorios = (limpio.match(new RegExp(ENVOLTORIOS.source, "g")) ?? []).length;
  return { tablas, envoltorios };
}

describe("una tabla ancha se desplaza, no se estruja", () => {
  const archivos = svelte(SRC).filter((f) => !f.endsWith(".test.js"));

  it("toda `<table>` está dentro de una región desplazable", () => {
    const descubiertas = [];
    for (const f of archivos) {
      const txt = readFileSync(f, "utf-8");
      const { tablas, envoltorios } = contar(txt);
      if (tablas === 0) continue;
      if (envoltorios < tablas) {
        descubiertas.push(
          `${f.replace(SRC + "/", "")} — ${tablas} tabla(s), ${envoltorios} envoltorio(s)`,
        );
      }
    }

    expect(
      descubiertas,
      "Una `<table width:100%>` con muchas columnas no desborda: COMPRIME las " +
        "celdas hasta partir el contenido, y se lee como una tabla rota sin que " +
        "nada delate el problema. Envuélvela en `.op-table-scroll` con " +
        '`role="region"`, `aria-label` y `tabindex="0"`.\n  ' +
        descubiertas.join("\n  "),
    ).toEqual([]);
  });

  it("cada región desplazable se puede recorrer con el teclado", () => {
    // Sin `tabindex="0"` a la tabla solo llega quien puede arrastrar: se cambia
    // un problema de lectura por uno de acceso.
    const incompletas = [];
    for (const f of archivos) {
      const txt = readFileSync(f, "utf-8");
      for (const m of sinComentarios(txt).matchAll(
        /<div\s+class="[^"]*op-table-scroll[^"]*"([^>]*)>/g,
      )) {
        const attrs = m[1];
        const falta = [];
        if (!attrs.includes('tabindex="0"')) falta.push("tabindex");
        if (!attrs.includes('role="region"')) falta.push("role");
        if (!attrs.includes("aria-label")) falta.push("aria-label");
        if (falta.length)
          incompletas.push(`${f.replace(SRC + "/", "")} — falta ${falta.join(", ")}`);
      }
    }
    expect(incompletas).toEqual([]);
  });

  it("control de vacuidad: el censo encuentra tablas de verdad", () => {
    // «No hay descubiertas» y «no miré nada» se ven igual. Si el extractor se
    // rompe —un cambio de marcado, un glob mal puesto—, los dos tests de arriba
    // pasarían vacíos para siempre.
    const total = archivos.reduce((n, f) => n + contar(readFileSync(f, "utf-8")).tablas, 0);
    expect(total).toBeGreaterThan(10);
  });
});
