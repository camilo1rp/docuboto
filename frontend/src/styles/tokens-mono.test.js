// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * El estilo de un identificador se define UNA vez y se hereda.
 *
 * Tres defectos convergían en lo mismo — no había un estilo base para `<code>`:
 *
 *  1. **Bulma pintaba `code` en rojo** (`#da1039`) sobre gris. En esta
 *     aplicación casi todo `<code>` es un número de radicado o un código de
 *     verificación, y el rojo es el color del error: el estilo por defecto
 *     contradecía el significado del dato. Se reparó **tres veces** pantalla por
 *     pantalla —en la consulta ciudadana salía rojo, en la cola de anulaciones
 *     el fondo se estiraba y se leía como un campo de formulario vacío— antes de
 *     que alguien mirara la hoja global.
 *  2. **`--op-font-mono` es un TAMAÑO** (12px), no una familia. Nueve
 *     declaraciones hacían `font-family: var(--op-font-mono, monospace)`, que el
 *     navegador descarta por inválida: el respaldo `monospace` NO se aplica
 *     —solo actúa si la variable no está definida—, así que el texto salía con
 *     la fuente heredada. Funcionaba de casualidad donde el elemento era
 *     `<code>`, que ya es monoespaciado por defecto del navegador.
 *  3. **Tres formas distintas** de pedir monoespaciado en 46 sitios.
 *
 * Estos tests vigilan la causa, no los síntomas.
 */
import { describe, expect, it } from "vitest";
import { readFileSync } from "node:fs";
import { execSync } from "node:child_process";

const TOKENS = readFileSync("src/styles/_tokens.scss", "utf-8");
const BULMA = readFileSync("src/styles/custom-bulma.scss", "utf-8");

function archivosSvelte() {
  return execSync("find src -name '*.svelte'", { encoding: "utf-8" }).trim().split("\n");
}

describe("el identificador se define una vez", () => {
  it("existe una familia monoespaciada de verdad, con pila de respaldo", () => {
    expect(TOKENS).toMatch(/\$op-font-family-mono:/);
    expect(TOKENS).toMatch(/--op-font-family-mono:/);
    // Una pila, no una palabra suelta: `monospace` a secas deja la elección al
    // sistema operativo y el mismo número se ve distinto en cada equipo.
    const bloque = TOKENS.slice(TOKENS.indexOf("$op-font-family-mono:"));
    expect(bloque.slice(0, 200)).toMatch(/Consolas|Menlo|SFMono/);
  });

  it("`code` NO hereda el rojo de Bulma", () => {
    // `#da1039` sobre un número de radicado dice «error» donde el dato es bueno.
    const regla = BULMA.slice(BULMA.indexOf("\ncode,"));
    expect(regla).toMatch(/color:\s*inherit/);
    expect(regla).toMatch(/background-color:\s*transparent/);
    expect(regla).toMatch(/font-family:\s*var\(--op-font-family-mono\)/);
  });

  it("nadie usa el token de TAMAÑO como familia tipográfica", () => {
    // La confusión que dejó nueve declaraciones inertes.
    const culpables = [];
    for (const ruta of [...archivosSvelte(), "src/styles/utilities.scss"]) {
      const texto = readFileSync(ruta, "utf-8");
      for (const m of texto.matchAll(/font-family:[^;]*--op-font-mono[^-][^;]*/g)) {
        culpables.push(`${ruta} → ${m[0].trim()}`);
      }
    }
    expect(culpables).toEqual([]);
  });

  it("no vuelve a haber tres formas de pedir monoespaciado", () => {
    const culpables = [];
    for (const ruta of archivosSvelte()) {
      const texto = readFileSync(ruta, "utf-8");
      for (const m of texto.matchAll(/font-family:\s*([^;]+);/g)) {
        const valor = m[1].trim();
        if (/mono/i.test(valor) && valor !== "var(--op-font-family-mono)") {
          culpables.push(`${ruta} → ${valor}`);
        }
      }
    }
    expect(culpables).toEqual([]);
  });

  it("el censo mira de verdad", () => {
    // Anti-vacuidad: si `find` fallara, los dos censos de arriba aprobarían sin
    // haber leído nada.
    expect(archivosSvelte().length).toBeGreaterThan(50);
  });
});
