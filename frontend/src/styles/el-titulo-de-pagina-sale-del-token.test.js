// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * el-titulo-de-pagina-sale-del-token.test.js — el tamaño de un `h1` lo decide el
 * sistema, no cada pantalla.
 *
 * La regla global solo fijaba la FAMILIA, así que el tamaño lo ponía quien
 * escribiera la pantalla: de las 49 con `<h1>`, **27 usaban `--op-font-xl`
 * (22 px), 16 `--op-font-lg` (18 px) y 5 ninguno**, heredando el 2em del
 * navegador. Tres tamaños para el mismo elemento y ninguno era una decisión.
 *
 * El pendiente decía «hace falta decidir cuál es el tamaño de un título», y la
 * tabla de la v1.0 ya lo decía: 28 px. Lo que no decía es que **no lo aplicaba
 * ni una sola pantalla**. Entre subir 43 vistas a un tamaño que nadie ha visto
 * nunca y adoptar el que 27 ya tienen —mirado por quien las construyó—, se
 * eligió lo segundo y se corrigió la anotación de la escala: un token que dice
 * ser el H1 y que ningún h1 usa es un comentario falso con forma de valor.
 *
 * Lo que este guardián NO puede medir: cuántos píxeles ocupa el título en
 * pantalla. Eso lo comprueba `e2e/adaptabilidad.spec.js` con un navegador de
 * verdad, porque jsdom no calcula layout.
 */
import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const AQUI = dirname(fileURLToPath(import.meta.url));
const SRC = resolve(AQUI, "..");
const TOKENS = join(resolve(AQUI), "_tokens.scss");
const GLOBAL = join(resolve(AQUI), "custom-bulma.scss");

/**
 * Pantallas con un motivo declarado para otro tamaño. Lista de ADMISIÓN: lo que
 * no está aquí y declara tamaño, cae.
 */
const EXCEPCIONES = new Map([["hero__title", "landing: clamp() de marca, escala con el viewport"]]);

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

/** Las clases que lleva puesto cada `<h1>` del template. */
function clasesDeH1(txt) {
  const finEstilo = txt.indexOf("<style");
  const tpl = finEstilo === -1 ? txt : txt.slice(0, finEstilo);
  const clases = [];
  for (const m of tpl.matchAll(/<h1[^>]*class="([^"]*)"/g)) {
    clases.push(...m[1].split(" ").filter((c) => c && !c.startsWith("{")));
  }
  return clases;
}

/**
 * El cuerpo de una regla SCSS, delimitado por su llave de cierre.
 *
 * Se cuenta llaves en vez de coger una ventana de N caracteres: una ventana
 * fija se cuela en la regla siguiente y el guardián acaba acusando al subtítulo
 * del tamaño del título. Pasó en el primer intento de este mismo fichero, y es
 * la misma trampa que el guardián del objetivo táctil, que llegó a medir su
 * propio comentario.
 */
function cuerpoDeRegla(css, desde) {
  let profundidad = 0;
  for (let i = desde; i < css.length; i += 1) {
    if (css[i] === "{") profundidad += 1;
    else if (css[i] === "}") {
      profundidad -= 1;
      if (profundidad === 0) return css.slice(desde, i);
    }
  }
  return css.slice(desde);
}

/** El `font-size` que declara el bloque de esa clase, si lo declara. */
function tamanoDeclarado(txt, clase) {
  const est = /<style[^>]*>([\s\S]*)<\/style>/.exec(txt);
  if (!est) return null;
  const base = clase.includes("__") ? clase.split("__")[1] : clase;
  for (const patron of [`\\.${clase}\\s*\\{`, `&__${base}\\s*\\{`]) {
    const m = new RegExp(patron).exec(est[1]);
    if (!m) continue;
    // `m.index + m[0].length - 1` apunta a la llave de apertura de la regla.
    const cuerpo = cuerpoDeRegla(est[1], m.index + m[0].length - 1);
    const fs = /font-size:\s*([^;]+);/.exec(cuerpo);
    return fs ? fs[1].trim() : null;
  }
  return null;
}

describe("el título de página sale del token", () => {
  const archivos = svelte(SRC).filter((f) => !f.endsWith(".test.js"));

  it("el token existe y la regla global lo aplica a `h1`", () => {
    expect(readFileSync(TOKENS, "utf-8")).toMatch(/\$op-font-title-page:/);
    // Sobre `h1` a secas, para alcanzar también a las que no declaran clase.
    expect(readFileSync(GLOBAL, "utf-8")).toMatch(
      /h1\s*\{\s*font-size:\s*var\(--op-font-title-page\);/,
    );
  });

  it("la escala ya no promete un H1 que ninguna pantalla usa", () => {
    // `$op-font-2xl` decía `// = H1 v1.0` y lo usaban cinco sitios, ninguno un
    // h1. Se conserva su valor —cambiarlo habría sido una regresión en esos
    // cinco— y se corrigió lo que afirmaba.
    const tokens = readFileSync(TOKENS, "utf-8");
    expect(/\$op-font-2xl:[^\n]*H1/.test(tokens)).toBe(false);
  });

  it("ninguna pantalla declara el tamaño de su propio `h1`", () => {
    const culpables = [];
    for (const f of archivos) {
      const txt = readFileSync(f, "utf-8");
      for (const clase of clasesDeH1(txt)) {
        if (EXCEPCIONES.has(clase)) continue;
        const tam = tamanoDeclarado(txt, clase);
        if (tam) culpables.push(`${f.replace(SRC + "/", "")} (.${clase}: ${tam})`);
      }
    }
    expect(
      culpables,
      "El tamaño de un título de página lo decide el sistema: así aparecieron " +
        "los tres tamaños distintos que había. Si esta pantalla tiene un motivo " +
        "real para otro, decláralo en la lista de excepciones.\n  " +
        culpables.join("\n  "),
    ).toEqual([]);
  });

  it("control de vacuidad: el censo encuentra los `h1` del árbol", () => {
    // «Ningún culpable» y «no miré nada» se ven igual.
    const total = archivos.reduce((n, f) => n + clasesDeH1(readFileSync(f, "utf-8")).length, 0);
    expect(total).toBeGreaterThan(40);
  });
});
