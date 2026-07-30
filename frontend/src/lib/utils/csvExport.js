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
