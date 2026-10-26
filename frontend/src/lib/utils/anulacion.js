// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * anulacion.js — Mapeos de estado de una solicitud de anulación
 * (GET /documents/{id}/anulacion), fuente única.
 *
 * Antes vivían duplicados literalmente en `/anulaciones/+page.svelte` y en
 * `/bandeja/+page.svelte` (M12, revisión UX Fase 0): en cuanto el backend
 * sume un cuarto `estado`, una de las dos copias habría quedado obsoleta sin
 * avisar. Ambas pantallas importan desde aquí.
 */

/** @type {Record<string,string>} Variante de StatusChip por `estado`. */
export const ANULACION_ESTADO_CHIP = {
  solicitada: "warning",
  aprobada: "error",
  rechazada: "neutral",
};

/** @type {Record<string,string>} Etiqueta visible por `estado`. */
export const ANULACION_ESTADO_LABEL = {
  solicitada: "Pendiente de decisión",
  aprobada: "Aprobada (radicado anulado)",
  rechazada: "Rechazada",
};
