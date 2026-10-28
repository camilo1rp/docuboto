// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

import { codigoError, mensajeError } from "$lib/utils/apiError.js";

/**
 * asistenteAcceso.js — Quién ve la capa de conocimiento (C-1, ADR-037).
 *
 * Dos condiciones, ambas necesarias (la UI espeja al backend, que re-valida):
 *  1. La ENTIDAD activó la capa (`asistente_habilitado` de `GET /auth/me`; el
 *     backend entrega `false` si no pudo comprobarlo — fail-closed). El nombre
 *     de la bandera se conserva; no hay asistente conversacional (ADR-046 §6).
 *  2. La persona tiene `PERM_CONOCIMIENTO` (nivel 1 consultar, nivel 2 aceptar una
 *     sugerencia).
 *
 * La búsqueda por conocimiento y «Sugerir con antecedentes» exigen las dos: las rutas
 * `/knowledge/*` piden bandera Y `PERM_CONOCIMIENTO` >= 1 (403 `forbidden` sin el permiso).
 */

export const PERM_CONOCIMIENTO = "PERM_CONOCIMIENTO";
export const NIVEL_CONOCIMIENTO_CONSULTAR = 1;
export const NIVEL_CONOCIMIENTO_ACEPTAR = 2;

/** Pestañas de /busqueda que llaman al servicio de conocimiento. */
export const TABS_CONOCIMIENTO = ["semantica", "antecedentes"];

/**
 * @param {{asistente_habilitado?: unknown}|null|undefined} user
 * @returns {boolean} solo `true` estricto: ausente, null o cualquier otro valor es apagado.
 */
export function asistenteActivo(user) {
  return user?.asistente_habilitado === true;
}

/**
 * @param {{asistente_habilitado?: unknown, permission_levels?: Record<string, number>}|null|undefined} user
 * @param {number} [minimo]
 */
export function puedeUsarConocimiento(user, minimo = NIVEL_CONOCIMIENTO_CONSULTAR) {
  if (!asistenteActivo(user)) return false;
  // ROOT tiene el permiso por definición (el backend lo exime del nivel).
  if ((user?.permission_levels?.USUA_PERM_ROOT ?? 0) > 0) return true;
  return (user?.permission_levels?.[PERM_CONOCIMIENTO] ?? 0) >= minimo;
}

/** Aceptar una sugerencia exige nivel 2 (editar), no solo consultar. */
export function puedeAceptarSugerencia(user) {
  return puedeUsarConocimiento(user, NIVEL_CONOCIMIENTO_ACEPTAR);
}

/**
 * Pestañas de /busqueda disponibles para esta sesión.
 * @param {Parameters<typeof puedeUsarConocimiento>[0]} user
 * @returns {string[]}
 */
export function tabsBusquedaPermitidas(user) {
  return puedeUsarConocimiento(user) ? ["exacta", ...TABS_CONOCIMIENTO] : ["exacta"];
}

/**
 * Mensaje legible para los errores de `/knowledge/*`.
 * @param {any} data cuerpo de la respuesta (plano o anidado en `detail`)
 * @param {string} porDefecto
 */
export function mensajeConocimiento(data, porDefecto) {
  const codigo = codigoError(data);
  if (codigo === "asistente_deshabilitado") {
    return "Su entidad no tiene activada la capa de conocimiento. Un administrador puede activarla en Administración > Parámetros.";
  }
  if (codigo === "forbidden") {
    return "No tiene permiso para usar la base de conocimiento (PERM_CONOCIMIENTO). Solicítelo a un administrador.";
  }
  if (codigo === "asistente_estado_no_disponible") {
    return "No se pudo comprobar si su entidad tiene activada la capa de conocimiento. Intente de nuevo en unos minutos.";
  }
  return mensajeError(data, porDefecto);
}
