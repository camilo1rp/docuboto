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
 * Un error que el `load` captura y la pantalla no lee es peor que no capturarlo.
 *
 * Defecto real (barrido negativo del plan de pruebas, B13): el `load` de
 * `/envios` capturaba cuidadosamente el 403 del gateway en `loadError`, lo
 * devolvía en `data`… y `+page.svelte` no lo mencionaba ni una vez. A un usuario
 * sin `PERM_RADI_SALIDA` la pantalla le decía **«No hay registros que coincidan
 * con los criterios seleccionados»**: afirmaba que la entidad no tiene envíos
 * postales cuando lo que ocurre es que no puede verlos. El mismo texto tapaba
 * además un servidor caído.
 *
 * El idioma correcto ya estaba en `/admin/colas`, que nombra el permiso que
 * falta y no finge un conjunto vacío.
 *
 * Este censo comprueba lo mínimo y mecánico: **todo campo de error que un
 * `+page.server.js` devuelve en su `load` aparece citado en su `+page.svelte`**.
 * No juzga cómo se pinta —eso es diseño— sino que no se tire a la basura.
 */
import { describe, expect, it } from "vitest";
import { readFileSync, existsSync } from "node:fs";
import { execSync } from "node:child_process";
import { dirname, join } from "node:path";

/** Nombres de campo que, por convención del proyecto, transportan un error. */
const CAMPOS_DE_ERROR = /^(loadError|error|errores|.*Error)$/;

function loadsDePagina() {
  return execSync("find 'src/routes' -name '+page.server.js'", { encoding: "utf-8" })
    .trim()
    .split("\n")
    .filter(Boolean);
}

/**
 * Campos que el `return` del `load` expone y que parecen un error.
 * Heurística deliberadamente simple: se leen las claves del objeto devuelto.
 */
export function camposDeErrorDevueltos(fuente) {
  const m = fuente.match(/return\s*\{([\s\S]*?)\n\s*\};/);
  if (!m) return [];
  return m[1]
    .split("\n")
    .map((l) => l.trim())
    .map((l) => (l.match(/^([A-Za-z_$][\w$]*)\s*[,:]/) || [])[1])
    .filter(Boolean)
    .filter((k) => CAMPOS_DE_ERROR.test(k));
}

describe("los errores de carga se leen en la pantalla", () => {
  it("detecta un campo de error en el return de un load (control positivo)", () => {
    const fuente = `export async function load() {
  return {
    shipments,
    total,
    loadError,
  };
}`;
    expect(camposDeErrorDevueltos(fuente)).toEqual(["loadError"]);
  });

  it("ningún +page.server.js devuelve un error que su pantalla ignore", () => {
    const loads = loadsDePagina();
    // Anti-vacuidad: si el descubrimiento falla, cae este test, no el censo.
    expect(loads.length).toBeGreaterThan(15);

    const culpables = [];
    for (const ruta of loads) {
      const campos = camposDeErrorDevueltos(readFileSync(ruta, "utf-8"));
      if (campos.length === 0) continue;
      const pagina = join(dirname(ruta), "+page.svelte");
      if (!existsSync(pagina)) continue;
      const markup = readFileSync(pagina, "utf-8");
      for (const campo of campos) {
        if (!markup.includes(campo)) {
          culpables.push(`${pagina} nunca menciona '${campo}' que su load devuelve`);
        }
      }
    }
    expect(culpables).toEqual([]);
  });
});
