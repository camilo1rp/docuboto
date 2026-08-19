// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * csvExport.js — Exportación de filas de DataTable a CSV, neutralizando la
 * inyección de fórmulas (Excel/Sheets/LibreOffice interpretan como fórmula
 * cualquier celda que empiece por un signo igual, más, menos, arroba, o un
 * control TAB/CR).
 *
 * Espejo exacto de `_csv_safe` en
 * `services/document-service/app/routers/reports.py` (E08, índice de
 * clasificados Ley 1712 art. 20): se antepone un apóstrofo para forzar la
 * lectura como texto. Este proyecto ya sufrió este hallazgo en el backend —
 * el cliente no puede ser una segunda puerta de entrada al mismo problema.
 */

/**
 * Neutraliza una celda potencialmente peligrosa para hojas de cálculo.
 * @param {unknown} value
 * @returns {unknown}
 */
export function escapeCsvFormulaInjection(value) {
  if (
    typeof value === "string" &&
    value.length > 0 &&
    ["=", "+", "-", "@", "\t", "\r"].includes(value[0])
  ) {
    return "'" + value;
  }
  return value;
}

/**
 * Serializa una celda CSV: aplica el escape anti-fórmula, comillas dobles si
 * contiene separador/comillas/salto de línea, y normaliza `null`/`undefined`
 * a cadena vacía.
 * @param {unknown} rawValue
 * @returns {string}
 */
export function csvCell(rawValue) {
  if (rawValue === null || rawValue === undefined) return "";
  const safe = escapeCsvFormulaInjection(String(rawValue));
  const stringValue = typeof safe === "string" ? safe : String(safe);
  if (/[",\n\r]/.test(stringValue)) {
    return `"${stringValue.replace(/"/g, '""')}"`;
  }
  return stringValue;
}

/**
 * Construye un CSV (con BOM UTF-8, cabecera CRLF) a partir de columnas
 * visibles y filas. Ignora columnas de solo-presentación (`key` que empieza
 * por `_`, p. ej. una columna `_acciones`).
 * @param {Array<{key: string; label: string}>} columns
 * @param {Array<Record<string, unknown>>} rows
 * @returns {string}
 */
export function buildCsv(columns, rows) {
  const exportableColumns = columns.filter((col) => !col.key.startsWith("_"));
  const header = exportableColumns.map((col) => csvCell(col.label)).join(",");
  const lines = rows.map((row) => exportableColumns.map((col) => csvCell(row[col.key])).join(","));
  return "﻿" + [header, ...lines].join("\r\n");
}

/**
 * Dispara la descarga del CSV en el navegador. Debe llamarse SOLO desde
 * código de cliente (p. ej. un manejador de clic) — usa `document`/`URL`.
 * @param {string} csvContent
 * @param {string} filename
 */
export function downloadCsv(csvContent, filename) {
  const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

/**
 * Nombre del fichero del resumen de radicación de `/reportes`.
 *
 * Se llamaba `radicados.csv`, y eso prometía un listado de radicados: quien lo
 * abría encontraba `seccion,clave,valor` —el resumen estadístico, que es lo que
 * ese endpoint produce— y tenía que deducir por su cuenta que el archivo no era
 * el que su nombre anunciaba. Un fichero que sale del sistema y acaba en el
 * escritorio de otra persona no puede depender de esa deducción.
 *
 * El rango de fechas entra en el nombre cuando el filtro está puesto, porque
 * dos resúmenes del mismo tenant solo se distinguen por él.
 *
 * @param {string} [desde] ISO `AAAA-MM-DD` o cadena vacía
 * @param {string} [hasta] ISO `AAAA-MM-DD` o cadena vacía
 * @returns {string}
 */
export function nombreResumenRadicacion(desde, hasta) {
  const iso = /^\d{4}-\d{2}-\d{2}$/;
  const d = typeof desde === "string" && iso.test(desde) ? desde : "";
  const h = typeof hasta === "string" && iso.test(hasta) ? hasta : "";
  if (d && h) return `resumen-radicacion-${d}_${h}.csv`;
  if (d) return `resumen-radicacion-desde-${d}.csv`;
  if (h) return `resumen-radicacion-hasta-${h}.csv`;
  return "resumen-radicacion.csv";
}
