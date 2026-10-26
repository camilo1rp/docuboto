// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Reproceso de lotes de radicación con errores (decisión del usuario): los
 * ítems fallidos quedan marcados en el lote, se muestran con su motivo y la
 * persona decide si procesa los faltantes. Lógica pura, sin DOM.
 */

import { codigoError } from "$lib/utils/apiError.js";

/** Estados en los que el lote ya no avanza solo y el sondeo debe parar. */
export const ESTADOS_TERMINALES = ["completed", "completado_con_errores", "failed"];

/** @param {string|null|undefined} status */
export function loteTermino(status) {
  return ESTADOS_TERMINALES.includes(String(status));
}

/**
 * El botón «Procesar los faltantes» solo se ofrece con el permiso Y si el
 * backend dice que el lote es reprocesable (el backend re-valida).
 * @param {{reprocesable?: boolean}|null|undefined} lote
 * @param {string[]} permisos
 */
export function puedeReprocesar(lote, permisos) {
  return lote?.reprocesable === true && (permisos ?? []).includes("PERM_RADI");
}

/**
 * Ítems fallidos: `errores[]` del backend, o los `failed` de `items[]`.
 * @param {{errores?: any[], items?: any[]}|null|undefined} lote
 */
export function itemsFallidos(lote) {
  if (Array.isArray(lote?.errores) && lote.errores.length > 0) return lote.errores;
  return (lote?.items ?? []).filter((i) => i?.status === "failed");
}

/** Cuántos ítems se procesarían: `pending_items` o, en su defecto, fallidos. */
export function itemsAProcesar(lote) {
  if (Number.isFinite(lote?.pending_items) && lote.pending_items > 0) return lote.pending_items;
  return itemsFallidos(lote).length;
}

// Señales de texto técnico (excepciones, SQL, trazas): no se muestran como
// motivo principal a quien radica.
const TECNICO_RE =
  /traceback|exception|asyncpg|psycopg|sqlstate|violates|constraint|errno|timeout|connection|\bnull\b|\bkeyerror\b|\bhttp \d{3}\b|^\w+(\.\w+)+:|^\w*error:/i;

/**
 * Motivo legible + detalle técnico secundario de un ítem fallido.
 * @param {{error_message?: string|null, error_tipo?: string|null}} item
 * @returns {{motivo: string, detalle: string}}
 */
export function motivoDelFallo(item) {
  const crudo = (item?.error_message ?? "").trim();
  const tipo = (item?.error_tipo ?? "").trim();
  if (!crudo) {
    return { motivo: "Falló sin motivo registrado.", detalle: tipo };
  }
  if (TECNICO_RE.test(crudo)) {
    return {
      motivo: "No se pudo radicar este documento por un fallo del sistema.",
      detalle: tipo ? `${tipo}: ${crudo}` : crudo,
    };
  }
  return { motivo: crudo, detalle: tipo };
}

/** Frase de la confirmación previa al reproceso. */
export function textoConfirmacion(n) {
  const cuantos = n === 1 ? "1 documento" : `${n} documentos`;
  return `Se procesarán ${cuantos} que no se radicaron. Los documentos ya radicados conservan su número; no se renumeran ni se duplican.`;
}

/**
 * Mensaje en español para el POST de reproceso.
 * @param {number} status
 * @param {any} data
 */
export function mensajeReproceso(status, data) {
  const codigo = codigoError(data) ?? "";
  if (status === 404 || codigo === "lote_no_encontrado") {
    return "No se encontró el lote. Puede que haya sido eliminado o que no pertenezca a esta entidad.";
  }
  if (codigo === "lote_en_proceso") {
    return "El lote todavía se está procesando. Espere a que termine para reanudarlo.";
  }
  if (codigo === "nada_que_reprocesar") {
    return "No hay documentos pendientes de procesar en este lote.";
  }
  if (status === 403) return "No tiene permiso para radicar; no puede procesar los faltantes.";
  if (status === 503 || codigo === "connection_error") {
    return "No se pudo conectar al servidor de carga masiva. Intente de nuevo.";
  }
  return `No se pudo reanudar el lote (HTTP ${status}).`;
}

/**
 * Asunto de un ítem: `null` llega cuando el radicado está por encima del
 * clearance de quien consulta; se dice, no se deja un hueco engañoso.
 * @param {{subject?: string|null}} item
 */
export function asuntoDelItem(item) {
  const a = (item?.subject ?? "").trim();
  return a || "Asunto reservado";
}
