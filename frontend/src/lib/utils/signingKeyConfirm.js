// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * signingKeyConfirm.js — Frases de confirmación escrita para actos
 * destructivos sobre la credencial de firma personal PKI (E17/F4).
 *
 * Mismo patrón que `reassignConfirm.js` (E05): un clic en "Aceptar" no basta
 * para una acción que deja al titular sin poder firmar — debe TECLEAR la
 * frase. Dos frases distintas para dos actos distintos:
 *
 *  - REENROLL_CONFIRM_PHRASE: re-enrolar sobre una credencial 'activo' la
 *    REVOCA atómicamente (queda 'pendiente_emision', inútil hasta una
 *    reemisión que es un trámite OFFLINE) — `perfil/+page.svelte`.
 *  - ADMIN_REVOKE_CONFIRM_PHRASE: un administrador revoca la credencial de
 *    OTRO usuario sin que ese usuario lo sepa de antemano —
 *    `admin/seguridad/claves/+page.svelte`.
 *
 * Compartidas entre la pantalla (habilita el botón) y su proxy
 * (segunda barrera server-side — el cliente es potencialmente hostil, D-05).
 */

export const REENROLL_CONFIRM_PHRASE = "REENROLAR";
export const ADMIN_REVOKE_CONFIRM_PHRASE = "REVOCAR";

/**
 * @param {string} text
 * @returns {boolean}
 */
export function isReenrollConfirmed(text) {
  return typeof text === "string" && text.trim() === REENROLL_CONFIRM_PHRASE;
}

/**
 * @param {string} text
 * @returns {boolean}
 */
export function isAdminRevokeConfirmed(text) {
  return typeof text === "string" && text.trim() === ADMIN_REVOKE_CONFIRM_PHRASE;
}
