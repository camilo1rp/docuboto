// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * radicado.js — Mapeos de presentación de un radicado (tipo y estado), fuente
 * única. Mismo criterio que `anulacion.js`, y por la misma razón.
 *
 * `STATUS_LABELS` vivía solo dentro de `/bandeja/+page.svelte`, así que la
 * pantalla de éxito de `/radicar` —la que se ve justo después de radicar, y en
 * la que más gente repara— mostraba el valor crudo del backend: **«Estado:
 * registered»**, en inglés, al lado de «Tipo: Entrada» que sí estaba traducido.
 *
 * Un mapa de presentación encerrado en una pantalla no es reutilizable: la
 * siguiente que necesite lo mismo o lo copia (y se desincroniza en cuanto el
 * backend sume un estado) o no lo traduce, que fue lo que pasó aquí.
 */

/** @type {Record<string,string>} Etiqueta visible por `doc_type` (E/S/I). */
export const RADICADO_TIPO_LABEL = {
  E: "Entrada",
  S: "Salida",
  I: "Interno",
};

/** @type {Record<string,string>} Etiqueta visible por `status`. */
export const RADICADO_ESTADO_LABEL = {
  registered: "Radicado",
  distributed: "Distribuido",
  in_progress: "En trámite",
  closed: "Cerrado",
  // `anulado` lo escribe document-service al aprobar una anulación
  // (`UPDATE radicados SET status='anulado'`). Faltaba aquí, así que el estado
  // más consecuente de todos —el documento queda sin efecto y su número se
  // conserva— se pintaba crudo, en el mismo fichero cuyo docstring cuenta que
  // ya pasó con «registered».
  anulado: "Anulado",
};

/** @type {Record<string,string>} Variante de StatusChip por `status`. */
export const RADICADO_ESTADO_CHIP = {
  registered: "info",
  distributed: "pending",
  in_progress: "active",
  closed: "closed",
  anulado: "error",
};

/**
 * Etiqueta de un estado, con reserva honesta: si el backend suma un estado que
 * este mapa todavía no conoce, se devuelve el valor crudo en vez de una cadena
 * vacía. Ver un `en_revision` sin traducir es feo; ver un hueco donde debería
 * ir el estado de un documento oficial es peor.
 * @param {string|null|undefined} status
 * @returns {string}
 */
export function etiquetaEstadoRadicado(status) {
  if (!status) return "—";
  return RADICADO_ESTADO_LABEL[status] ?? status;
}
