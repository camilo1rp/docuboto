// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * el-ancho-de-pagina-sale-del-token.test.js — el ancho de una página no se
 * decide pantalla por pantalla.
 *
 * El censo encontró **27 pantallas con nueve anchos distintos** —1200, 1100,
 * 1000, 960, 900, 820, 760, 720 y 640— y **19 alineadas a la izquierda**, así
 * que el espacio sobrante quedaba todo a la derecha. En un portátil de 1536 px
 * con la barra lateral quedan 1272 px útiles: una pantalla de datos acotada a
 * 900 px desperdiciaba 372 px **y aun así recortaba su tabla**.
 *
 * Ninguno de esos nueve valores era una decisión: eran nueve personas
 * resolviendo lo mismo por separado. Ahora hay dos, y cada uno responde a una
 * pregunta distinta: `--op-content-max` no lleva techo —la pantalla de datos se
 * ajusta al dispositivo que la usa—, y `--op-reading-max` sí, porque ahí el
 * límite no lo pone el sitio disponible sino cuánto texto se lee de un renglón.
 *
 * Este guardián mira el CONTENEDOR RAÍZ de cada página. No toca los `max-width`
 * interiores: acotar una tarjeta, un aviso o una columna de formulario es
 * legítimo y frecuente, y prohibirlo haría un guardián ruidoso.
 */
import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const AQUI = dirname(fileURLToPath(import.meta.url));
const RUTAS = resolve(AQUI, "..", "routes");
const TOKENS = join(resolve(AQUI), "_tokens.scss");

/**
 * Páginas que fijan su ancho a mano **a propósito**, con su motivo. La lista es
 * de ADMISIÓN: lo que no está aquí y no usa el token, cae.
 */
const EXCEPCIONES = new Map([
  // La ventanilla y la consulta públicas las usa un ciudadano en su móvil o en
  // un equipo prestado: son formularios cortos y deliberadamente estrechos, no
  // pantallas de trabajo.
  ["(public)/radicacion", "formulario público, estrecho a propósito"],
  ["(public)/consulta", "formulario público, estrecho a propósito"],
  // La landing tiene su propia retícula de marca.
  ["(public)", "landing con retícula propia"],
  // El catálogo de componentes es una demo, no una pantalla del producto.
  ["(app)/_kit", "catálogo de componentes"],
]);

function paginas(dir, base = "") {
  /** @type {{ruta: string, archivo: string}[]} */
  const salida = [];
  for (const e of readdirSync(dir, { withFileTypes: true })) {
    const ruta = join(dir, e.name);
    if (e.isDirectory()) salida.push(...paginas(ruta, base ? `${base}/${e.name}` : e.name));
    else if (e.name === "+page.svelte") salida.push({ ruta: base, archivo: ruta });
  }
  return salida;
}

/** La clase del primer elemento del template — el contenedor raíz de la página. */
function contenedorRaiz(txt) {
  const finScript = txt.lastIndexOf("</script>");
  const m = /<(?:div|main|section|article)\s+class="([\w-]+)[\s"]/.exec(
    txt.slice(finScript === -1 ? 0 : finScript),
  );
  return m ? m[1] : null;
}

function anchoDeclarado(txt, clase) {
  const est = /<style[^>]*>([\s\S]*)<\/style>/.exec(txt);
  if (!est) return null;
  const bloque = new RegExp(`\\.${clase}\\s*\\{([^}]*)\\}`).exec(est[1]);
  if (!bloque) return null;
  const mw = /max-width:\s*([^;]+);/.exec(bloque[1]);
  return mw ? mw[1].trim() : null;
}

describe("el ancho de página sale del token", () => {
  it("la pantalla de datos NO lleva techo: se ajusta al dispositivo", () => {
    // Un número fijo aquí es lo mismo que había —alguien decidiendo el ancho de
    // una pantalla que no tiene delante—, solo que una vez en vez de nueve. En
    // un monitor de 2560 sobraría más de la mitad; en uno de 1366, justo el que
    // recortaba las tablas, no cambiaría nada.
    const tokens = readFileSync(TOKENS, "utf-8");
    expect(tokens).toMatch(/\$op-content-max:\s*100%/);
  });

  it("el de lectura SÍ lleva límite, y por un motivo que no es el sitio", () => {
    // Un campo de una columna a 1.200 px es incómodo y una línea de 200
    // caracteres se pierde al saltar de renglón. Se ajusta igual hacia abajo:
    // `max-width` nunca ensancha, así que en un móvil sigue al 100 %.
    const tokens = readFileSync(TOKENS, "utf-8");
    const lectura = /\$op-reading-max:\s*(\d+)px/.exec(tokens);
    expect(lectura).toBeTruthy();
    expect(Number(lectura[1])).toBeLessThan(1000);
  });

  it("ninguna página acota su contenedor raíz con un número escrito a mano", () => {
    const culpables = [];
    for (const { ruta, archivo } of paginas(RUTAS)) {
      if (EXCEPCIONES.has(ruta)) continue;
      const txt = readFileSync(archivo, "utf-8");
      const clase = contenedorRaiz(txt);
      if (!clase) continue;
      const ancho = anchoDeclarado(txt, clase);
      if (ancho && /\d+px/.test(ancho)) culpables.push(`${ruta} (.${clase}: ${ancho})`);
    }

    expect(
      culpables,
      "El ancho de una página es una decisión del sistema, no de cada pantalla: " +
        "así nacieron los nueve valores distintos que había. Usa " +
        "`var(--op-content-max)` si la pantalla muestra datos o " +
        "`var(--op-reading-max)` si es un formulario o texto seguido; si de " +
        "verdad necesita otro, decláralo en la lista de excepciones con su motivo.\n  " +
        culpables.join("\n  "),
    ).toEqual([]);
  });

  it("y de verdad está mirando el árbol: cuenta las que SÍ usan el token", () => {
    // Sin esto el test de arriba daría verde el día que `paginas()` devolviera
    // una lista vacía o `contenedorRaiz()` dejara de reconocer el marcado: no
    // encontrar culpables y no mirar nada se ven exactamente igual.
    let conToken = 0;
    for (const { archivo } of paginas(RUTAS)) {
      const txt = readFileSync(archivo, "utf-8");
      const clase = contenedorRaiz(txt);
      if (!clase) continue;
      const ancho = anchoDeclarado(txt, clase);
      if (ancho && ancho.includes("--op-content-max")) conToken += 1;
      if (ancho && ancho.includes("--op-reading-max")) conToken += 1;
    }
    // Eran 27 pantallas acotadas; 24 pasaron a token y 3 quedaron declaradas
    // como excepción. El suelo se pone por debajo para que añadir o quitar una
    // pantalla no rompa el guardián, pero un cero o un puñado sí lo rompen.
    expect(conToken).toBeGreaterThan(15);
  });

  it("control positivo: el guardián sabe encontrar un ancho a mano", () => {
    // Sin esto, el test de arriba pasaría vacío para siempre el día que el
    // extractor dejara de funcionar — por un cambio de marcado, por ejemplo.
    const falso = `<div class="x-page">hola</div>
      <style>.x-page { max-width: 900px; }</style>`;
    expect(anchoDeclarado(falso, contenedorRaiz(falso))).toBe("900px");
  });
});
