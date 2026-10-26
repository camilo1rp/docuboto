// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * desclasificacionConfirm.js — Confirmación escalonada de APROBAR una
 * desclasificación (C-2, ADR-039).
 *
 * Aprobar baja el nivel de un radicado clasificado en la misma transacción y la
 * información deja de estar protegida: en la práctica no se deshace (se difunde).
 * Un clic en «Aceptar» no basta. Dos escalones, en este orden:
 *   1. declarar que se revisó el radicado (la pantalla ofrece el enlace);
 *   2. teclear el NÚMERO DEL RADICADO que se va a desclasificar.
 * El segundo obliga a mirar CUÁL radicado es y evita aprobar la fila equivocada
 * de una cola. Solo si ambos se cumplen se habilita el botón final; el proxy
 * exige además `confirmado: true` (segunda barrera: el cliente es hostil).
 */

/**
 * Texto que hay que teclear para confirmar: el número del radicado.
 * @param {{tracking_number?: string|null}|null|undefined} fila
 * @returns {string}
 */
export function fraseAprobacion(fila) {
  return String(fila?.tracking_number ?? "").trim();
}

/**
 * @param {{revisado: boolean, texto: string, fila: {tracking_number?: string|null}|null|undefined}} p
 * @returns {boolean}
 */
export function aprobacionConfirmada({ revisado, texto, fila }) {
  const frase = fraseAprobacion(fila);
  return revisado === true && frase !== "" && String(texto ?? "").trim() === frase;
}
