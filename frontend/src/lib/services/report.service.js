// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * report.service.js — Cliente del servicio de reportes/estadísticas (E09).
 *
 * Endpoint real: GET /api/v1/reports/radicados
 * Respuesta (ReportSummary):
 *   { total, by_doc_type, by_status, by_month, by_dependencia }
 *
 * Todas las llamadas pasan por el api-gateway con token + X-Tenant-Slug (ADR-011).
 */

import { apiFetch } from "$lib/services/api.service.js";

/**
 * Obtiene el resumen estadístico de radicados del tenant.
 *
 * @param {object} [opts]
 * @param {string} [opts.token]
 * @param {string} [opts.tenant]
 * @param {string} [opts.dateFrom]
 * @param {string} [opts.dateTo]
 * @returns {Promise<import('./report.service.js').ReportSummary>}
 */
export async function fetchReportSummary({
  token = "",
  tenant = "",
  dateFrom = "",
  dateTo = "",
} = {}) {
  const params = new URLSearchParams();
  if (dateFrom) params.set("date_from", dateFrom);
  if (dateTo) params.set("date_to", dateTo);

  const qs = params.toString() ? `?${params.toString()}` : "";
  return apiFetch(`/reports/radicados${qs}`, { token, tenant });
}

/**
 * @typedef {object} MonthCount
 * @property {string} month  — 'YYYY-MM'
 * @property {number} count
 */

/**
 * @typedef {object} ReportSummary
 * @property {number} total
 * @property {Record<string,number>} by_doc_type   — { E: n, S: n, I: n }
 * @property {Record<string,number>} by_status      — { registered: n, ... }
 * @property {MonthCount[]}           by_month
 * @property {Record<string,number>} by_dependencia — { nombre_dep: n, ... }
 */
