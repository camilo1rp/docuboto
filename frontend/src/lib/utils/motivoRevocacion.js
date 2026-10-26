// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * motivoRevocacion.js — Vocabulario cerrado del motivo de revocación de una
 * credencial de firma (RFC 5280 reasonCode). Espejo de `MotivoRevocacion` en
 * auth-service (CHECK en la migración tenant 019); el backend siempre
 * re-valida. `key_compromise` invalida hacia atrás las firmas previas en
 * /verify, por eso el motivo ya no es texto libre.
 */

/**
 * Tipado como Array (no ReadonlyArray) porque `FormField.options` declara un
 * array mutable y svelte-check rechazaba pasarle este: es el vocabulario
 * cerrado y nadie lo modifica, pero el tipo lo tiene que aceptar el componente.
 * @type {Array<{value: string, label: string}>}
 */
export const MOTIVOS_REVOCACION = [
  { value: "key_compromise", label: "Clave comprometida o sospecha de compromiso" },
  { value: "superseded", label: "Reemplazada por otra credencial" },
  { value: "cessation_of_operation", label: "Cese de la función o salida del funcionario" },
  { value: "affiliation_changed", label: "Cambio de dependencia o de cargo" },
  { value: "privilege_withdrawn", label: "Retiro de la facultad de firmar" },
  { value: "unspecified", label: "Otro motivo" },
];

const _VALIDOS = new Set(MOTIVOS_REVOCACION.map((m) => m.value));

/**
 * @param {unknown} v
 * @returns {string | null} el código si pertenece al vocabulario; si no, null
 */
export function motivoRevocacionValido(v) {
  return typeof v === "string" && _VALIDOS.has(v) ? v : null;
}
