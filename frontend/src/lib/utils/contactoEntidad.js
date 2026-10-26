// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * contactoEntidad.js — el contacto que la entidad DECLARA para sus canales
 * alternos (presencial / correo). Los campos no declarados llegan en `null`;
 * no se inventa ninguno (no hay horario declarado: tampoco se muestra uno).
 */

const CLAVES = ["nombre", "direccion", "telefono", "email", "web"];

/**
 * Deja solo las claves declaradas, con texto no vacío; `null` si no queda ninguna.
 * @param {any} crudo
 * @returns {{nombre:string|null,direccion:string|null,telefono:string|null,email:string|null,web:string|null}|null}
 */
export function normalizarContacto(crudo) {
  if (!crudo || typeof crudo !== "object") return null;
  /** @type {Record<string, string|null>} */
  const limpio = {};
  let alguno = false;
  for (const k of CLAVES) {
    const v = typeof crudo[k] === "string" ? crudo[k].trim().slice(0, 300) : "";
    limpio[k] = v || null;
    if (v) alguno = true;
  }
  return alguno ? /** @type {any} */ (limpio) : null;
}
