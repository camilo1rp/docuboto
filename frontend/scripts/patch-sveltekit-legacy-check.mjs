// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

// Rollup 4.64.6 deja en el bundle la rama de Svelte 5 de
// `@sveltejs/kit/src/runtime/client/state.svelte.js`. En el SSR de Svelte 4,
// `onMount` se imprime como `function onMount() {\n}` y la expresión
// `/function \w+\(\) \{\}/` no la reconoce: `is_legacy` queda en falso y cada
// página que importa `$app/stores` revienta con
// `ReferenceError: $state is not defined` (sveltejs/kit#17425). Kit 2 ya no
// acepta el arreglo. Se admite el espacio en blanco antes de compilar.

import { readFileSync, writeFileSync } from "node:fs";

const ruta = "node_modules/@sveltejs/kit/src/runtime/client/state.svelte.js";
const origen = "/function \\w+\\(\\) \\{\\}/";
const destino = "/function \\w+\\(\\)\\s*\\{\\s*\\}/";
const src = readFileSync(ruta, "utf8");

if (src.includes(destino)) {
  process.exit(0);
}
if (!src.includes(origen)) {
  console.error("no esta el chequeo de legado de Kit en " + ruta);
  process.exit(1);
}
writeFileSync(ruta, src.replace(origen, destino));
