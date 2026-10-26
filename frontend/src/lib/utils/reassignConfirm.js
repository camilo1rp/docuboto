// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * reassignConfirm.js — Frase de confirmación escrita para la reasignación en
 * cascada (E05, RF-FLU-08). Acción masiva e irreversible: un clic en
 * "Aceptar" no basta (Sistema de Diseño §confirmación destructiva); el
 * administrador debe TECLEAR esta frase.
 *
 * Compartido entre `/admin/flujos/reasignacion/+page.svelte` (habilita el
 * botón de ejecución solo cuando coincide) y su proxy
 * `api/+server.js` (segunda barrera server-side — el cliente es
 * potencialmente hostil, D-05).
 */

export const REASSIGN_CONFIRM_PHRASE = "REASIGNAR";

/**
 * @param {string} text
 * @returns {boolean}
 */
export function isReassignConfirmed(text) {
  return typeof text === "string" && text.trim() === REASSIGN_CONFIRM_PHRASE;
}
