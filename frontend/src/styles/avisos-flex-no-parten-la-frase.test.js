// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Guardián: un aviso en `display: flex` no puede llevar la frase suelta.
 *
 * El defecto que cierra este archivo se vio en `/admin/interoperabilidad`, en el
 * paso de confirmar una importación — o sea, en el aviso que explica una
 * **escritura oficial** justo antes de ejecutarla. El contenedor era
 * `display: flex` y dentro había un icono, texto suelto y un `<strong>` con el
 * nombre del fichero. Flex convierte cada nodo suelto en su propia columna: la
 * frase se leía partida en tres bloques desalineados, con el nombre del paquete
 * flotando en medio de la advertencia.
 *
 * Lo que hace difícil de cazar a este defecto es que **no rompe nada
 * verificable**: el HTML es válido, el texto está entero, `svelte-check` pasa, y
 * cualquier test que busque la frase con `getByText` la encuentra. Solo se ve
 * mirando la pantalla, que es justo lo que no escala a 36 vistas.
 *
 * Por eso el control es estructural y por CENSO: se recorren los `.svelte` del
 * proyecto, se leen las clases que su propio `<style>` declara `display: flex`,
 * y se exige que ningún párrafo con una de esas clases mezcle texto suelto con
 * marcado inline. La regla que impone —envolver el texto en un `<span>`— es la
 * corrección misma, no un rodeo para contentar al test.
 */
import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

/** `src/` del proyecto: este archivo vive en `src/styles/`. */
const RAIZ = resolve(dirname(fileURLToPath(import.meta.url)), "..") + "/";

/** Marcado inline que, junto a texto suelto, delata una frase partida. */
const INLINE = /<(strong|em|b|code|abbr)\b/;

function svelteFiles(dir) {
  const salida = [];
  for (const entrada of readdirSync(dir)) {
    const ruta = join(dir, entrada);
    if (statSync(ruta).isDirectory()) salida.push(...svelteFiles(ruta));
    else if (entrada.endsWith(".svelte")) salida.push(ruta);
  }
  return salida;
}

/** Clases que el `<style>` de ESE archivo declara como flex. */
function clasesFlex(fuente) {
  const i = fuente.indexOf("<style");
  if (i < 0) return new Set();
  const estilo = fuente.slice(i);
  const clases = new Set();
  // SCSS ANIDA: `.aviso { display: flex; &__detail { … } }`. Un `[^{}]*` tras
  // la llave de apertura se para en la primera llave interna y se pierde el
  // bloque entero — así fue como la primera versión de este guardián dejó
  // pasar el propio defecto que lo motivó, y solo se supo por mutación. Las
  // declaraciones directas de un selector van SIEMPRE antes de cualquier
  // bloque anidado, así que basta leer hasta la primera llave siguiente.
  const selector = /\.([a-zA-Z0-9_-]+)[^{};]*\{/g;
  let m;
  while ((m = selector.exec(estilo)) !== null) {
    const resto = estilo.slice(m.index + m[0].length);
    const corte = resto.search(/[{}]/);
    const directas = corte === -1 ? resto : resto.slice(0, corte);
    if (/display:\s*(inline-)?flex/.test(directas)) clases.add(m[1]);
  }
  return clases;
}

function parrafos(fuente) {
  const salida = [];
  const bloque = /<p class="([^"]+)"[^>]*>([\s\S]*?)<\/p>/g;
  let m;
  while ((m = bloque.exec(fuente)) !== null) {
    salida.push({ clases: m[1], cuerpo: m[2] });
  }
  return salida;
}

describe("avisos flex — la frase no se parte en columnas", () => {
  const archivos = svelteFiles(RAIZ);

  it("el censo encuentra pantallas: si no, este guardián aprueba por vacío", () => {
    // Control del propio control. Con `RAIZ` mal resuelta, `archivos` sería
    // `[]` y el test de abajo pasaría siempre sin haber mirado nada — el modo
    // de fallo más caro de un guardián, porque se lee como cobertura.
    expect(archivos.length).toBeGreaterThan(60);
  });

  it("ningún párrafo flex mezcla texto suelto con <strong>/<code>/<em>", () => {
    const rotos = [];
    for (const archivo of archivos) {
      const fuente = readFileSync(archivo, "utf8");
      const flex = clasesFlex(fuente);
      if (flex.size === 0) continue;
      for (const p of parrafos(fuente)) {
        if (!p.clases.split(/\s+/).some((c) => flex.has(c))) continue;
        if (!INLINE.test(p.cuerpo)) continue;
        const suelto = p.cuerpo
          .replace(/<!--[\s\S]*?-->/g, "")
          .replace(/<(strong|em|b|code|abbr)\b[\s\S]*?<\/\1>/g, "")
          .replace(/<span[\s\S]*?<\/span>/g, "")
          .replace(/<i\b[\s\S]*?<\/i>/g, "")
          .replace(/<[^>]+>/g, "")
          .replace(/\{[^}]*\}/g, "")
          .trim();
        if (suelto.length > 0) {
          rotos.push(`${archivo.replace(RAIZ, "src/")} :: "${suelto.slice(0, 60)}…"`);
        }
      }
    }

    expect(
      rotos,
      "En un contenedor `display: flex` cada nodo suelto es una COLUMNA, así " +
        "que la frase se lee partida con el trozo en <strong> flotando en " +
        "medio. Envuelve el texto del aviso en un único <span>. Afectados:\n" +
        rotos.join("\n"),
    ).toEqual([]);
  });
});
