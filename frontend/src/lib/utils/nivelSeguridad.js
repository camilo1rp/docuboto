// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * nivelSeguridad.js — Mapeos de nivel de seguridad documental (Ley 1712 / RF-SEG-08).
 *
 * Fuente única para etiquetas, opciones de select y variante de StatusChip.
 * Importar desde componentes y páginas que muestren o editen el nivel_seguridad.
 * No duplicar estos valores con hex sueltos ni strings locales.
 */

/**
 * Opciones para el selector de nivel de seguridad.
 * `value` es string para compatibilidad con FormField type="select".
 *
 * @type {Array<{value: string; label: string}>}
 */
export const NIVEL_OPTIONS = [
  { value: "1", label: "1 — Pública" },
  { value: "2", label: "2 — Reservada" },
  { value: "3", label: "3 — Clasificada" },
];

/**
 * Mapeo nivel_seguridad (entero) → { label, status } para StatusChip.
 * Proporciones:  1=Pública → success (verde), 2=Reservada → warning, 3=Clasificada → error.
 *
 * @type {Record<number, {label: string; status: string}>}
 */
export const NIVEL_CHIP_MAP = {
  1: { label: "Pública", status: "success" },
  2: { label: "Reservada", status: "warning" },
  3: { label: "Clasificada", status: "error" },
};

/**
 * Devuelve las props de StatusChip para un nivel de seguridad dado.
 * Si `nivel` es null/undefined/desconocido, devuelve estado neutral ("Sin clasificar").
 *
 * @param {number|null|undefined} nivel
 * @returns {{ label: string; status: string }}
 */
export function getNivelChip(nivel) {
  return (
    NIVEL_CHIP_MAP[/** @type {number} */ (nivel)] ?? { label: "Sin clasificar", status: "neutral" }
  );
}
