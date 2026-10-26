// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Ubicación ACTUAL de un radicado (ADR-029) frente a la dependencia de RADICACIÓN.
 *
 * `dest_dept` es a quién iba dirigido el documento: una instantánea legal fijada al
 * radicar. Dónde está el radicado AHORA es `ubicacion_dept`, que el backend deriva
 * (la del flujo si ya movió el radicado; si no, la de radicación). Pintar `dest_dept`
 * como «dónde está» fue el defecto que originó el ADR: un radicado repartido a Talento
 * Humano y devuelto a Control Interno seguía diciendo «Oficina de Tecnología».
 *
 * Aquí NO hay fallback a `dest_dept`: si el backend no manda `ubicacion_dept`, es que
 * no sabe dónde está, y mostrar la de radicación sería inventarlo.
 */

/**
 * @param {{ubicacion_dept?: string|null}|null|undefined} doc
 * @returns {string|null} Nombre de la dependencia donde está ahora, o null si se ignora.
 */
export function ubicacionActual(doc) {
  const nombre = doc?.ubicacion_dept;
  return typeof nombre === "string" && nombre.trim() ? nombre : null;
}

/**
 * Texto de una celda de lista: el nombre o el guion largo.
 * @param {{ubicacion_dept?: string|null}|null|undefined} doc
 */
export function ubicacionTexto(doc) {
  return ubicacionActual(doc) ?? "—";
}

/**
 * Los estados terminales congelan la ubicación (ADR-029 §3): dicen DÓNDE terminó,
 * no dónde está en trámite.
 * @param {{status?: string|null, ubicacion_dept?: string|null}|null|undefined} doc
 * @returns {string|null} «Cerrado en X» / «Anulado en X»; null si no es terminal o no hay dato.
 */
export function ubicacionTerminal(doc) {
  const nombre = ubicacionActual(doc);
  if (!nombre) return null;
  if (doc?.status === "closed") return `Cerrado en ${nombre}`;
  if (doc?.status === "anulado") return `Anulado en ${nombre}`;
  return null;
}

/**
 * ¿La ubicación viene del flujo (el radicado ya se movió) o es todavía la de radicación?
 * @param {{flujo_dept?: string|null}|null|undefined} doc
 */
export function ubicadoPorFlujo(doc) {
  return typeof doc?.flujo_dept === "string" && doc.flujo_dept.trim() !== "";
}

/**
 * Quién tiene el radicado. `flujo_usuario` es un `auth_users.id` (nunca se pinta crudo:
 * un UUID no le dice nada a quien opera). Resuelve contra un mapa id→usuario, y si es
 * quien mira, «Tú». Sin resolución devuelve null: mejor omitir la línea que mostrar el UUID.
 * @param {{flujo_usuario?: string|null}|null|undefined} doc
 * @param {string|null|undefined} usuarioActualId  auth_users.id de quien mira
 * @param {Record<string,string>|null|undefined} usuariosPorId  id → username
 * @returns {string|null}
 */
export function responsableDe(doc, usuarioActualId, usuariosPorId) {
  const id = doc?.flujo_usuario;
  if (!id) return null;
  if (usuarioActualId && id === usuarioActualId) return "Tú";
  return usuariosPorId?.[id] ?? null;
}

/**
 * Etiqueta del detalle: dice DÓNDE está el radicado y, cuando el flujo todavía no lo ha
 * movido (`flujo_dept` NULL = sin pasos, ADR-029 §3), lo dice con todas las letras en vez
 * de dejar que «Ubicación actual: X» parezca un reparto. Una Salida/Interno sin
 * `dest_dept` toma `origin_dept` (ADR-029 §2): ahí X es la dependencia PRODUCTORA, no un
 * destino.
 * @param {{status?: string|null, doc_type?: string|null, dest_dept?: string|null,
 *          flujo_dept?: string|null, ubicacion_dept?: string|null}|null|undefined} doc
 * @returns {string|null} null si el backend no dice dónde está.
 */
export function ubicacionEtiqueta(doc) {
  const nombre = ubicacionActual(doc);
  if (!nombre) return null;
  const terminal = ubicacionTerminal(doc);
  if (ubicadoPorFlujo(doc)) return terminal ?? nombre;

  const tieneDestino = typeof doc?.dest_dept === "string" && doc.dest_dept.trim() !== "";
  const productora = !tieneDestino && (doc?.doc_type === "S" || doc?.doc_type === "I");
  const lugar = productora ? `la dependencia productora: ${nombre}` : nombre;
  if (doc?.status === "closed") return `Cerrado sin pasar por el flujo — en ${lugar}`;
  if (doc?.status === "anulado") return `Anulado sin pasar por el flujo — en ${lugar}`;
  return `Sin repartir — en ${lugar}`;
}

/**
 * Línea de responsable del detalle, o null si no hay nada que decir (terminal, sin
 * pasos o sin poseedor). Si el poseedor no se puede nombrar (el perfil no puede leer el
 * catálogo de usuarios), lo dice en genérico en vez de omitir o de pintar el UUID.
 * @param {{status?: string|null, flujo_dept?: string|null, flujo_usuario?: string|null}|null|undefined} doc
 * @param {string|null|undefined} usuarioActualId
 * @param {Record<string,string>|null|undefined} usuariosPorId
 * @returns {string|null}
 */
export function responsableTexto(doc, usuarioActualId, usuariosPorId) {
  if (!ubicadoPorFlujo(doc) || !doc?.flujo_usuario) return null;
  if (doc.status === "closed" || doc.status === "anulado") return null;
  const nombre = responsableDe(doc, usuarioActualId, usuariosPorId);
  return nombre ? `Responsable: ${nombre}` : "A cargo de un responsable de la dependencia";
}
