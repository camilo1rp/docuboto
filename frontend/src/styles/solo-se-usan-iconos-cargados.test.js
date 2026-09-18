// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * solo-se-usan-iconos-cargados.test.js — Ningún icono de una familia que no se
 * carga.
 *
 * El layout importaba `all.min.css`, que arrastra `solid`, `regular` y `brands`
 * con sus tres fuentes: el navegador se descargaba `fa-brands-400.woff2`
 * (116 KB) y `fa-regular-400.woff2` (25 KB) para iconos que esta aplicación no
 * usa **ni una vez** — medido: 771 usos de `fas`/`fa-solid`, cero de `far`/`fab`.
 *
 * Ahora solo se cargan el núcleo y `solid`, y por eso hace falta este guardián:
 * **el modo de fallo es mudo**. Un `fab fa-github` escrito mañana no da error ni
 * aviso; el glifo simplemente no existe en la fuente cargada y se pinta una caja
 * vacía. Igual que un `var(--op-*)` inexistente, solo se ve mirando la pantalla
 * y sabiendo cómo tenía que verse.
 *
 * Si algún día se necesita una familia nueva, se importa su CSS en
 * `+layout.svelte` y se añade aquí: las dos mitades del trato —«uso esta
 * familia» y «la cargo»— quedan atadas, que es justo lo que no estaba.
 */
import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const AQUI = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(AQUI, "..");
const LAYOUT = join(SRC, "routes", "+layout.svelte");

/** Familias cargadas hoy. Añadir una exige importar su CSS (ver abajo). */
const CARGADAS = new Set(["fas", "fa-solid"]);

/** Cualquier familia de Font Awesome escrita en el árbol. */
const FAMILIA =
  /\b(fa[srlbd]|fa-solid|fa-regular|fa-brands|fa-light|fa-thin|fa-duotone)\b(?=\s+fa-)/g;

function archivos(dir) {
  /** @type {string[]} */
  const salida = [];
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const ruta = join(dir, e.name);
    if (e.isDirectory()) salida.push(...archivos(ruta));
    else if (e.isFile() && (ruta.endsWith(".svelte") || ruta.endsWith(".js"))) salida.push(ruta);
  }
  return salida;
}

describe("iconos: solo se usan familias que el layout carga", () => {
  const todos = archivos(SRC).filter((f) => !f.endsWith(".test.js"));

  it("el censo lee algo: un guardián vacío aprobaría por vacuidad", () => {
    const usos = todos.flatMap((f) => [...readFileSync(f, "utf8").matchAll(FAMILIA)]);
    expect(usos.length).toBeGreaterThan(300);
  });

  it("el layout carga el núcleo y la familia solid, y no `all`", () => {
    const layout = readFileSync(LAYOUT, "utf8");
    expect(layout).toContain("fontawesome-free/css/fontawesome.min.css");
    expect(layout).toContain("fontawesome-free/css/solid.min.css");
    // `all` volvería a traerse las tres fuentes sin que nadie lo decidiera.
    expect(layout).not.toContain("fontawesome-free/css/all");
  });

  it("ningún icono pertenece a una familia que no se carga", () => {
    /** @type {string[]} */
    const culpables = [];
    for (const f of todos) {
      const texto = readFileSync(f, "utf8");
      for (const m of texto.matchAll(FAMILIA)) {
        if (!CARGADAS.has(m[1])) {
          const linea = texto.slice(0, m.index).split("\n").length;
          culpables.push(`${f.slice(SRC.length + 1)}:${linea} → ${m[1]}`);
        }
      }
    }
    expect(culpables).toEqual([]);
  });
});
