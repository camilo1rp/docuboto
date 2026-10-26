// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * trdFases.server.js — validación compartida de las fases de retención y de las
 * dependencias productoras de una serie TRD (C-4, ADR-040).
 *
 * La retención tiene UNA fuente: `archivo_gestion_years` + `archivo_central_years`
 * (obligatorias, sin valor por omisión; 0 es válido, la suma debe ser >= 1).
 * `retention_years` y `total_retention` ya NO se reenvían nunca: el backend los
 * rechaza con 422 `extra_forbidden` y los publica solo como derivados de lectura.
 */

import { json } from "@sveltejs/kit";

/** @param {unknown} v @returns {number|null} entero >= 0 o null */
export function aEnteroNoNegativo(v) {
  if (v === null || v === undefined || v === "" || typeof v === "boolean") return null;
  const n = Number(v);
  return Number.isInteger(n) && n >= 0 ? n : null;
}

/**
 * Valida las fases. `obligatorias` (alta) exige ambas; en PATCH/versión solo se
 * validan las presentes. Devuelve `{ payload }` con las fases válidas o `{ error }`.
 * @param {Record<string, any>} body
 * @param {boolean} obligatorias
 * @returns {{ payload?: Record<string, number>, error?: Response }}
 */
export function leerFases(body, obligatorias) {
  /** @type {Record<string, number>} */
  const payload = {};
  for (const [campo, etiqueta] of /** @type {const} */ ([
    ["archivo_gestion_years", "archivo de gestión"],
    ["archivo_central_years", "archivo central"],
  ])) {
    const crudo = body?.[campo];
    const ausente = crudo === undefined || crudo === null || crudo === "";
    if (ausente) {
      if (obligatorias) {
        return {
          error: json(
            {
              error: "validation_error",
              detail: `Los años de ${etiqueta} son obligatorios (0 es válido).`,
            },
            { status: 400 },
          ),
        };
      }
      continue;
    }
    const n = aEnteroNoNegativo(crudo);
    if (n === null) {
      return {
        error: json(
          {
            error: "validation_error",
            detail: `Los años de ${etiqueta} deben ser un entero mayor o igual a 0.`,
          },
          { status: 400 },
        ),
      };
    }
    payload[campo] = n;
  }
  if (obligatorias && payload.archivo_gestion_years + payload.archivo_central_years < 1) {
    return {
      error: json(
        {
          error: "validation_error",
          detail: "La retención total debe sumar al menos 1 año entre gestión y central.",
        },
        { status: 400 },
      ),
    };
  }
  if (
    "archivo_gestion_years" in payload &&
    "archivo_central_years" in payload &&
    payload.archivo_gestion_years + payload.archivo_central_years < 1
  ) {
    return {
      error: json(
        {
          error: "validation_error",
          detail: "La retención total debe sumar al menos 1 año entre gestión y central.",
        },
        { status: 400 },
      ),
    };
  }
  return { payload };
}

/**
 * Lista de ids (enteros positivos) de `dependencias.codigo`, sin repetidos.
 * @param {unknown} v
 * @returns {{ value?: number[], error?: Response }}
 */
export function leerProductoras(v) {
  if (!Array.isArray(v)) {
    return {
      error: json(
        { error: "validation_error", detail: "Las dependencias productoras deben ser una lista." },
        { status: 400 },
      ),
    };
  }
  /** @type {number[]} */
  const out = [];
  for (const x of v) {
    const n = typeof x === "boolean" || x === "" || x === null ? NaN : Number(x);
    if (!Number.isInteger(n) || n <= 0) {
      return {
        error: json(
          { error: "validation_error", detail: "Dependencia productora inválida." },
          { status: 400 },
        ),
      };
    }
    if (!out.includes(n)) out.push(n);
  }
  return { value: out };
}
