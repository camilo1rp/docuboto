// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * causal.js — Opciones de causal para un acto concreto.
 *
 * El catálogo `causales` del tenant declara, desde la migración tenant 008, a qué
 * acto pertenece cada fila (`ambito`). Antes ese dato estaba escrito DOS veces
 * —en el prefijo del código (`DEV-`/`ANU-`) y en el del nombre («Devolución: …»)—
 * y declarado ninguna, así que ninguna pantalla podía filtrarlo sin adivinar: los
 * modales de devolver y anular acabaron pidiendo el código a mano, con ejemplos
 * («INCOMPLETO», «DUPLICADO») que ni siquiera existían en el catálogo.
 *
 * El filtro vive aquí y no dentro de la pantalla porque el fallo que importa es
 * SILENCIOSO: si deja de filtrar, el modal de devolución ofrece «Anulación:
 * radicado duplicado» como causal válida y nadie lo nota hasta leer un informe.
 */

/** Ámbito por defecto de una causal recién creada. */
export const AMBITO_GENERAL = "general";

/**
 * Causales aplicables a un acto, como opciones de `<select>`.
 *
 * `general` entra SIEMPRE: es el ámbito por defecto, y esconderlo haría que
 * administrar una causal nueva no sirviera de nada hasta acordarse de
 * clasificarla. Una fila sin `ambito` (catálogo anterior a la migración) se trata
 * como `general` por el mismo motivo: mejor ofrecerla de más que perderla.
 *
 * @param {Array<{codigo?: string, nombre?: string, ambito?: string|null}>} causales
 * @param {string} ambito
 * @returns {Array<{value: string, label: string}>}
 */
export function causalesDe(causales, ambito) {
  return (causales ?? [])
    .filter((c) => c && c.codigo)
    .filter((c) => c.ambito === ambito || !c.ambito || c.ambito === AMBITO_GENERAL)
    .map((c) => ({ value: c.codigo, label: c.nombre ? `${c.codigo} — ${c.nombre}` : c.codigo }));
}
