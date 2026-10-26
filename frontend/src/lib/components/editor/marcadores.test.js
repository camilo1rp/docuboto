// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * marcadores.test.js — Tecleando `*NOM_R*` queda `*NOM_R*`, no `<em>NOM_R</em>`.
 *
 * Se monta un Editor de TipTap REAL y se simula la escritura carácter a
 * carácter. Probar el regex por su cuenta no habría servido de nada: el regex
 * ya era correcto cuando el defecto existía —lo que fallaba era quién ganaba la
 * pulsación, la cursiva de Markdown o el marcador—, y eso solo lo decide el
 * editor con las dos reglas montadas a la vez.
 */
import { describe, it, expect, beforeEach, afterEach } from "vitest";
// Estos tests necesitan las DOS cosas: el DOM (montan nodos reales) y leer
// ficheros del árbol. No pueden llevar la directiva de entorno «node» de
// Vitest —perderían
// `document`— y bajo jsdom el `import` de `node:fs` resuelve al sustituto del
// navegador, que no exporta nada (`readFileSync is not a function`), porque
// `vite.config.js` fuerza la condición "browser" bajo Vitest para que
// `onMount` sea el real. `process.getBuiltinModule` devuelve el módulo de Node
// auténtico sin pasar por la resolución de Vite, así que sirve para ambos.
// Comprobado con una sonda: `document` disponible Y lectura de fichero OK.
// Ojo al redactar aquí: Vitest busca el token de esa directiva por
// expresión regular en TODO el archivo, comentarios incluidos. Escribirlo
// literalmente —aunque sea para decir que NO se usa— se lo aplica al
// archivo y le quita el DOM. Pasó al escribir esta misma nota.
const { readFileSync } = process.getBuiltinModule("fs");
import { crearProteccionDeMarcadores } from "./marcadores.js";

/** @type {any} */ let core;
/** @type {any} */ let StarterKit;
/** @type {HTMLElement} */ let host;

beforeEach(async () => {
  core = await import("@tiptap/core");
  StarterKit = (await import("@tiptap/starter-kit")).default;
  host = document.createElement("div");
  document.body.appendChild(host);
});

afterEach(() => host.remove());

/**
 * Escribe `texto` en el editor tal y como lo haría una persona: un carácter por
 * transacción, que es lo único que dispara las reglas de entrada.
 */
function teclear(editor, texto) {
  for (const ch of texto) {
    const { from } = editor.state.selection;
    editor.view.someProp("handleTextInput", (f) => f(editor.view, from, from, ch)) ||
      editor.view.dispatch(editor.state.tr.insertText(ch, from, from));
  }
  return editor.getHTML();
}

function editorCon({ proteger }) {
  return new core.Editor({
    element: host,
    extensions: [
      ...(proteger ? [crearProteccionDeMarcadores(core)] : []),
      StarterKit.configure({}),
    ],
    content: "",
  });
}

describe("protección de marcadores de combinación", () => {
  it("SIN protección, la cursiva se come el marcador (el defecto)", () => {
    // Este caso fija el comportamiento que había: es lo que hacía que toda la
    // combinación de E23 fuera inalcanzable desde la pantalla de plantillas.
    const editor = editorCon({ proteger: false });
    expect(teclear(editor, "Radicado *RAD_S* fin")).toContain("<em>RAD_S</em>");
    editor.destroy();
  });

  it("CON protección, el marcador sobrevive tal cual", () => {
    const editor = editorCon({ proteger: true });
    const html = teclear(editor, "Radicado *RAD_S* fin");
    expect(html).toContain("*RAD_S*");
    expect(html).not.toContain("<em>");
    editor.destroy();
  });

  it("la cursiva normal sigue funcionando", () => {
    // La protección casa solo con la FORMA del vocabulario (mayúsculas). Si se
    // llevara por delante toda la cursiva por asteriscos, sería un cambio de
    // comportamiento en el editor que nadie pidió.
    const editor = editorCon({ proteger: true });
    const html = teclear(editor, "esto es *importante* de verdad");
    expect(html).toContain("<em>importante</em>");
    editor.destroy();
  });

  it("protege todos los marcadores de una línea, no solo el primero", () => {
    const editor = editorCon({ proteger: true });
    const html = teclear(editor, "*NOM_R* y *DEPE_NOM*");
    expect(html).toContain("*NOM_R*");
    expect(html).toContain("*DEPE_NOM*");
    expect(html).not.toContain("<em>");
    editor.destroy();
  });
});

describe("qué editores activan la protección", () => {
  // Aserción sobre el CÓDIGO de las vistas, no sobre su comportamiento: montar
  // TipTap dentro del cajón de cada pantalla en jsdom cuesta más de lo que
  // aporta. Lo que esta prueba impide es lo que de verdad pasó — tener la
  // extensión escrita y probada, y no enchufarla en la pantalla que la
  // necesita.
  const fuente = (ruta) => readFileSync(ruta, "utf-8");

  it("plantillas y borradores la activan: su texto alimenta la combinación", () => {
    for (const ruta of [
      "src/routes/(app)/admin/plantillas/+page.svelte",
      "src/routes/(app)/borradores/+page.svelte",
    ]) {
      expect(fuente(ruta), ruta).toMatch(/<RichEditor[\s\S]{0,400}?protegerMarcadores/);
    }
  });

  it("radicar NO la activa: allí no hay combinación y `*texto*` debe ser cursiva", () => {
    // Anti-vacuidad: si la protección estuviera siempre puesta, la prueba de
    // arriba pasaría sin decir nada.
    expect(fuente("src/routes/(app)/radicar/+page.svelte")).not.toMatch(
      /<RichEditor[\s\S]{0,400}?protegerMarcadores/,
    );
  });
});
