// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * prestamo.js — Prestatario del préstamo de una unidad física (C-5 / C11).
 *
 * El prestatario es una UNIÓN DISCRIMINADA por `tipo`:
 *  - `interno`: una persona del sistema, por `user_id` = `auth_users.id`. Nunca
 *    el `sub` de Keycloak (también es un UUID: la forma no lo distingue, el
 *    backend lo rechaza con 422 `prestatario_inexistente`).
 *  - `externo`: nombre + tipo y número de documento obligatorios; entidad,
 *    cargo, correo y teléfono opcionales (minimización, Ley 1581).
 *
 * `prestado_por` NO viaja jamás: quién entrega lo resuelve el servidor.
 */

export const DOCUMENTO_TIPOS = ["CC", "CE", "NIT", "PA", "TI", "PEP", "PPT"];

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const OPCIONALES = ["entidad", "cargo", "correo", "telefono"];

/**
 * Arma el cuerpo del POST campo a campo (allowlist, anti mass-assignment).
 * @param {any} prestatario
 * @param {string} fechaDevolucion YYYY-MM-DD
 * @param {any} [observaciones]
 * @returns {{ok: Record<string, any>} | {error: string}}
 */
export function construirPayloadPrestamo(prestatario, fechaDevolucion, observaciones) {
  if (!prestatario || typeof prestatario !== "object") {
    return { error: "Indique quién recibe la unidad (interno o externo)." };
  }
  /** @type {Record<string, any>} */
  let p;
  if (prestatario.tipo === "interno") {
    const uid = typeof prestatario.user_id === "string" ? prestatario.user_id.trim() : "";
    if (!UUID_RE.test(uid)) {
      return { error: "Elija a la persona interna de la lista de elegibles." };
    }
    p = { tipo: "interno", user_id: uid };
  } else if (prestatario.tipo === "externo") {
    const nombre = typeof prestatario.nombre === "string" ? prestatario.nombre.trim() : "";
    const dt = typeof prestatario.documento_tipo === "string" ? prestatario.documento_tipo : "";
    const dn =
      typeof prestatario.documento_numero === "string" ? prestatario.documento_numero.trim() : "";
    if (nombre.length < 3) return { error: "El nombre del solicitante externo es obligatorio." };
    if (!DOCUMENTO_TIPOS.includes(dt) || dn.length < 3) {
      return {
        error:
          "El tipo y el número de documento (mínimo 3 caracteres) del solicitante externo son obligatorios.",
      };
    }
    p = { tipo: "externo", nombre, documento_tipo: dt, documento_numero: dn };
    for (const k of OPCIONALES) {
      const v = typeof prestatario[k] === "string" ? prestatario[k].trim() : "";
      if (v) p[k] = v;
    }
  } else {
    return { error: "El tipo de prestatario debe ser interno o externo." };
  }
  /** @type {Record<string, any>} */
  const ok = { prestatario: p, fecha_devolucion_esperada: fechaDevolucion.trim() };
  if (typeof observaciones === "string" && observaciones.trim()) {
    ok.observaciones = observaciones.trim();
  }
  return { ok };
}

/**
 * Nombre a mostrar del prestatario de una fila de préstamo.
 * @param {any} p
 * @returns {string}
 */
export function prestatarioEtiqueta(p) {
  if (!p) return "—";
  const nombre = p.prestatario_nombre ?? p.tercero_nombre ?? p.prestado_a ?? "—";
  const tipo =
    p.prestatario_tipo === "interno"
      ? "interno"
      : p.prestatario_tipo === "externo"
        ? "externo"
        : "";
  return tipo ? `${nombre} (${tipo})` : nombre;
}
