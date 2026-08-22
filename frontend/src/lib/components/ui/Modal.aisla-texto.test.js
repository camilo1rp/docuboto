// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * El modal no hereda el estilo de texto del sitio donde se montó.
 *
 * Encontrado confirmando la derogación de una TRD, que es un acto
 * irreversible. El diálogo se renderiza dentro de `.catalog-crud__actions`,
 * un contenedor que declara `white-space: nowrap` para que no se partan los
 * botones de una fila de la tabla. El modal heredaba ese `nowrap`: el párrafo
 * que explica la consecuencia («da de baja el instrumento…») se extendía
 * 1361 px dentro de un cuadro de 400 y aparecía cortado, con una barra de
 * desplazamiento horizontal. Quien confirmaba sin arrastrarla estaba decidiendo
 * sobre un acto que no se deshace **sin haber leído lo que hace**.
 *
 * jsdom no calcula cascada de verdad, así que el control se hace sobre la
 * declaración: el componente tiene que neutralizar explícitamente las
 * propiedades de texto que se heredan. Es un control de intención, y por eso
 * el archivo dice también POR QUÉ, para que nadie lo borre por parecer trivial.
 */
import { describe, it, expect } from "vitest";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const AQUI = dirname(fileURLToPath(import.meta.url));
const FUENTE = readFileSync(resolve(AQUI, "Modal.svelte"), "utf8");

/** Bloque `<style>` del componente. */
const ESTILO = FUENTE.slice(FUENTE.indexOf("<style"));

describe("Modal — aislamiento tipográfico", () => {
  it("neutraliza `white-space`, que es el que cortó el aviso de la derogación", () => {
    expect(ESTILO).toMatch(/white-space:\s*normal/);
  });

  it("neutraliza también las demás propiedades de texto heredables", () => {
    // `nowrap` fue la que se notó; las otras tres se heredan igual y producen
    // el mismo tipo de sorpresa (un aviso en versalitas, centrado o espaciado
    // porque el modal cayó dentro de una cabecera de tabla).
    for (const propiedad of [
      /text-align:\s*left/,
      /text-transform:\s*none/,
      /letter-spacing:\s*normal/,
    ]) {
      expect(ESTILO).toMatch(propiedad);
    }
  });

  it("el cuerpo del modal no fuerza scroll horizontal por sí mismo", () => {
    // `overflow-x: auto` en el cuerpo es lo que convirtió el desbordamiento en
    // una barra que hay que arrastrar en vez de en texto que envuelve. Con el
    // texto envolviendo ya no aparece, pero si alguien vuelve a poner
    // `nowrap` arriba, este control deja dicho dónde mirar.
    const cuerpo = ESTILO.slice(ESTILO.indexOf("&__body"));
    expect(cuerpo.slice(0, 400)).not.toMatch(/white-space:\s*nowrap/);
  });
});
