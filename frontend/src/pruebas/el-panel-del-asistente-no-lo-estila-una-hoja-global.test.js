// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * el-panel-del-asistente-no-lo-estila-una-hoja-global.test.js
 *
 * Historia del defecto que vigila: `src/styles/layout.scss` traía desde el
 * andamiaje una regla `.assistant-dock { transform: translateX(100%) }`, de un
 * diseño en el que el panel estaba siempre montado y se mostraba con `.is-open`.
 * El componente actual se monta con `{#if open}` y nunca pone esa clase. Su
 * estilo propio no declara `transform`, así que no pisaba el de la hoja global:
 * al abrir el asistente, el fondo se oscurecía y el panel quedaba FUERA de la
 * pantalla. Medido el 2026-10-01 en el navegador: `x=1901` con un viewport de
 * 1916.
 *
 * Nada lo delataba: la prueba e2e comprobaba `toBeVisible()`, que en Playwright
 * solo exige caja no vacía y que el elemento no esté oculto — un panel
 * desplazado fuera del viewport es «visible» para esa aserción. Ahora la e2e
 * exige `toBeInViewport`, y este guardián corta la causa: una hoja global no
 * debe estilar la clase raíz de un componente que trae sus estilos con ámbito,
 * porque cualquier propiedad que el componente no declare la gana la global en
 * silencio.
 */
import { describe, it, expect } from "vitest";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const RAIZ = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const ESTILOS = join(RAIZ, "styles");

/** Quita comentarios SCSS (`//` y `/* *\/`) para no tomar prosa por reglas. */
function sinComentarios(scss) {
  return scss.replace(/\/\*[\s\S]*?\*\//g, "").replace(/(^|[^:])\/\/.*$/gm, "$1");
}

function hojasGlobales() {
  return readdirSync(ESTILOS)
    .filter((f) => f.endsWith(".scss"))
    .map((f) => ({ nombre: f, css: sinComentarios(readFileSync(join(ESTILOS, f), "utf8")) }));
}

describe("el panel del asistente solo lo estila su componente", () => {
  it("hay hojas globales que revisar (anti-vacuidad)", () => {
    // Si la carpeta cambiara de sitio, el guardián de abajo aprobaría sobre una
    // lista vacía y no vigilaría nada.
    expect(hojasGlobales().length).toBeGreaterThan(3);
  });

  it("ninguna hoja de src/styles declara un selector `.assistant-dock`", () => {
    const culpables = hojasGlobales()
      .filter(({ css }) => /\.assistant-dock(?![\w-])/.test(css))
      .map(({ nombre }) => nombre);

    expect(
      culpables,
      "Una hoja global estila `.assistant-dock`: cualquier propiedad que " +
        "AssistantDock.svelte no declare la gana esa regla en silencio — así quedó " +
        "el panel fuera de la pantalla. Los estilos del panel van en el componente.",
    ).toEqual([]);
  });

  it("control positivo: el detector sí reconoce la regla que causó el defecto", () => {
    // Sin esto, un regex roto daría `[]` siempre y la prueba de arriba sería inerte.
    const regla = ".assistant-dock {\n  transform: translateX(100%);\n}";
    expect(/\.assistant-dock(?![\w-])/.test(sinComentarios(regla))).toBe(true);
    // Y no confunde los elementos BEM del propio componente con la clase raíz.
    expect(/\.assistant-dock(?![\w-])/.test(".assistant-dock__header {}")).toBe(false);
  });
});
