// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * radicarBorradorCuerpo.js — Lista de admisión del cuerpo de radicar un borrador.
 * Vive fuera de `+server.js` (SvelteKit solo admite exportar verbos HTTP allí).
 */

import { METADATA_RESERVADA } from "./schemaField.js";

/** Tope del cuerpo que el proxy acepta (bytes de texto); el real es mucho menor. */
export const CUERPO_MAX = 64 * 1024;

/** @param {unknown} v */
function esCodigo(v) {
  return typeof v === "number" && Number.isInteger(v) && v >= 1 && v <= 999999;
}

/**
 * Deja pasar SOLO `tipo_documental`, `origin_dept_code`, `dest_dept_code` y
 * `metadata` (límites de `BorradorRadicar`). `nivel_seguridad` u otra clave que
 * llegue se descarta; un valor mal tipado se rechaza, no se corrige.
 * @param {unknown} entrada
 * @returns {{cuerpo: Record<string, unknown>, error?: undefined}|{error: string, cuerpo?: undefined}}
 */
export function filtrarCuerpoRadicar(entrada) {
  if (!entrada || typeof entrada !== "object" || Array.isArray(entrada)) {
    return { error: "El cuerpo debe ser un objeto." };
  }
  const e = /** @type {Record<string, unknown>} */ (entrada);
  /** @type {Record<string, unknown>} */
  const salida = {};
  if (e.tipo_documental != null) {
    if (typeof e.tipo_documental !== "string" || e.tipo_documental.length > 100) {
      return { error: "El tipo documental no es válido." };
    }
    if (e.tipo_documental.trim() !== "") salida.tipo_documental = e.tipo_documental.trim();
  }
  for (const k of ["origin_dept_code", "dest_dept_code"]) {
    if (e[k] == null) continue;
    if (!esCodigo(e[k])) return { error: `El código de dependencia (${k}) no es válido.` };
    salida[k] = e[k];
  }
  if (e.metadata != null) {
    if (typeof e.metadata !== "object" || Array.isArray(e.metadata)) {
      return { error: "Los metadatos deben ser un objeto." };
    }
    // Las claves reservadas las pone el sistema; el backend las prohíbe de
    // entrada. Se rechazan de forma explícita (no se descartan en silencio): un
    // cliente que las manda tiene un defecto que conviene ver.
    const reservada = Object.keys(e.metadata).find((k) =>
      Object.prototype.hasOwnProperty.call(METADATA_RESERVADA, k),
    );
    if (reservada)
      return { error: `La clave de metadatos «${reservada}» está reservada al sistema.` };
    if (Object.keys(e.metadata).length > 0) salida.metadata = e.metadata;
  }
  return { cuerpo: salida };
}
