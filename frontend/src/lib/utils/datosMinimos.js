// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * datosMinimos.js — Datos mínimos de un radicado (Acuerdo AGN 060/2001 §5.2).
 *
 * ESPEJO de `REQUERIDOS_POR_TIPO` en
 * `services/document-service/app/core/datos_minimos.py`. El backend re-valida
 * siempre; esto solo evita ofrecer «Radicar» cuando la API respondería 422.
 * El guardián `datos-minimos-paridad.test.js` compara ambos diccionarios: si el
 * backend cambia la regla y esto no, el test cae.
 */

/** @type {Record<string, readonly string[]>} */
export const REQUERIDOS_POR_TIPO = {
  E: ["doc_class", "dest_dept_code"],
  I: ["doc_class", "dest_dept_code"],
  S: ["doc_class", "origin_dept_code"],
};

/**
 * Campos del §5.2 que faltan (mismos nombres que el backend).
 * @param {string} docType
 * @param {{doc_class?: string|null, origin_dept_code?: number|string|null, dest_dept_code?: number|string|null}} valores
 * @returns {string[]}
 */
export function faltantes(docType, valores) {
  const v = /** @type {Record<string, unknown>} */ (valores ?? {});
  return (REQUERIDOS_POR_TIPO[docType] ?? []).filter((campo) => {
    const x = v[campo];
    return x == null || (typeof x === "string" && x.trim() === "");
  });
}

/** Nombre legible de cada campo para los avisos. */
export const ETIQUETA_CAMPO = {
  doc_class: "el tipo documental",
  origin_dept_code: "la dependencia de origen",
  dest_dept_code: "la dependencia destino",
};
