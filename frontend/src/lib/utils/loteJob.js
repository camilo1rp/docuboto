// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Cómo se presenta un ítem de un lote (carga masiva, E11).
 *
 * El panel de progreso decidía con un ternario: `success ? "OK" : "Error"`. Un
 * lote recién encolado tiene todos sus ítems en `pending`, así que la pantalla
 * anunciaba **«Error» en cada uno** mientras el contador de al lado —que sí leía
 * el dato bueno— decía «0 con error». Y como el motivo se pintaba tal cual, y en
 * un ítem pendiente `error_message` es `null`, el texto que se leía era
 * literalmente «null».
 *
 * Los tres estados que el backend distingue (`pending`, `success`, `failed`) se
 * distinguen también aquí. Y un ítem fallido SIN motivo dice que no se sabe el
 * motivo, en vez de imprimir el hueco.
 */

/**
 * @typedef {{item_index: number, status: string, result_id: string|null, error_message: string|null}} ItemDeLote
 */

/**
 * Chip y etiqueta para un ítem del lote.
 * @param {string|null|undefined} status
 * @returns {{chip: "success"|"error"|"neutral", etiqueta: string}}
 */
export function presentacionDelItem(status) {
  if (status === "success") return { chip: "success", etiqueta: "OK" };
  if (status === "failed") return { chip: "error", etiqueta: "Error" };
  if (status === "processing") return { chip: "neutral", etiqueta: "En curso" };
  if (status === "pending") return { chip: "neutral", etiqueta: "En cola" };
  // Un estado que no conocemos no se disfraza de éxito ni de error: se muestra
  // tal cual, que es lo único honesto que se puede hacer con él.
  return { chip: "neutral", etiqueta: String(status ?? "—") };
}

/**
 * Texto de detalle de un ítem: nunca `null`, nunca una promesa vacía.
 * @param {ItemDeLote} item
 * @returns {string}
 */
export function detalleDelItem(item) {
  if (item?.status === "success") return item.result_id ?? "";
  if (item?.status === "failed") {
    const motivo = (item.error_message ?? "").trim();
    return motivo || "Falló sin motivo registrado.";
  }
  if (item?.status === "processing") return "Procesando…";
  return "En espera de proceso.";
}
