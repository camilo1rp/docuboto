// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * overlayStack.js — Registro de superposiciones (Modal/Drawer) abiertas.
 *
 * Modal y Drawer coexisten a menudo (p. ej. un Modal de confirmación lanzado
 * desde dentro de un Drawer de detalle de radicado, sin cerrar el Drawer —
 * ver `(app)/bandeja/+page.svelte`). Ambos componentes registran su propio
 * listener `keydown` en `document` para Tab (trampa de foco) y Escape.
 *
 * Sin coordinación, con Drawer y Modal abiertos a la vez, un Escape pensado
 * para cerrar el Modal (el que está "encima") también dispararía el cierre
 * del Drawer de fondo, porque el listener del Drawer no sabe que hay algo
 * apilado sobre él. Este módulo lleva una pila simple de ids de instancia;
 * cada componente solo reacciona a Tab/Escape si es el tope de la pila.
 *
 * Estado a nivel de módulo (no un store de Svelte): es un detalle de
 * coordinación interno de Modal/Drawer, no estado de UI observado por otros
 * componentes.
 */

/** @type {string[]} */
let stack = [];

/** @param {string} id */
export function pushOverlay(id) {
  if (!stack.includes(id)) stack = [...stack, id];
}

/** @param {string} id */
export function popOverlay(id) {
  if (stack.includes(id)) stack = stack.filter((x) => x !== id);
}

/** @param {string} id */
export function isTopOverlay(id) {
  return stack.length > 0 && stack[stack.length - 1] === id;
}

/** Solo para tests: vacía la pila entre casos para evitar fugas de estado. */
export function _resetOverlayStackForTests() {
  stack = [];
}
