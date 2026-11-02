// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * inteligencia.js — Textos de la pantalla «Inteligencia» (ADR-045 §4).
 *
 * La declaración que escribe el administrador tiene que contener, tal cual,
 * el nombre y el id del proveedor y de cada subencargado. En un nivel, también
 * la ruta (`installation` o `hosted`), los alias de modelo y los subencargados
 * de esa ruta. Esos textos salen de la declaración firmada; aquí no hay un
 * nombre escrito a mano.
 */

/** @type {Record<number, string>} */
export const ETIQUETA_NIVEL = { 1: "PÚBLICA", 2: "RESERVADA", 3: "CLASIFICADA" };

const CODIGO = { 1: "public", 2: "reserved", 3: "classified" };

/**
 * @param {string[]} piezas
 * @returns {string[]}
 */
function unicos(piezas) {
  /** @type {string[]} */
  const salida = [];
  for (const pieza of piezas) {
    if (pieza && !salida.includes(pieza)) salida.push(pieza);
  }
  return salida;
}

/**
 * @param {{
 *   provider: {name: string, id: string},
 *   subprocessors?: {name: string, id: string}[],
 * }} vista
 * @returns {string[]}
 */
export function exigidosProveedor(vista) {
  const piezas = [vista.provider.name, vista.provider.id];
  for (const sub of vista.subprocessors ?? []) piezas.push(sub.name, sub.id);
  return unicos(piezas);
}

/**
 * @param {{routing?: {level: string}[]} | null | undefined} vista
 * @param {number} nivel
 */
export function filaDe(vista, nivel) {
  const codigo = CODIGO[nivel];
  if (!codigo || !vista) return null;
  return (vista.routing ?? []).find((fila) => fila.level === codigo) ?? null;
}

/**
 * @param {{subprocessors?: {id: string, name: string}[], routing?: object[]}} vista
 * @param {number} nivel
 * @returns {string[] | null}
 */
export function exigidosNivel(vista, nivel) {
  const fila = filaDe(vista, nivel);
  if (!fila) return null;
  const nombres = Object.fromEntries((vista.subprocessors ?? []).map((sub) => [sub.id, sub.name]));
  const piezas = [fila.hosting, ...fila.model_aliases];
  for (const id of fila.subprocessors ?? []) {
    piezas.push(id);
    if (nombres[id]) piezas.push(nombres[id]);
  }
  return unicos(piezas);
}

/** Un modelo alojado solo vale si no retiene ni entrena con los datos. */
export function rutaAdmitida(fila) {
  if (!fila) return false;
  if (fila.hosting === "installation") return true;
  return fila.prompt_and_output_retention === "none" && fila.trains_on_customer_data === false;
}

/**
 * @param {{
 *   provider: {name: string, id: string},
 *   subprocessors?: {name: string, id: string}[],
 * }} vista
 */
export function declaracionProveedor(vista) {
  const subs = (vista.subprocessors ?? []).map((sub) => `${sub.name} (${sub.id})`).join(", ");
  const cola = subs ? ` Subencargados: ${subs}.` : "";
  return `La entidad transmite al proveedor ${vista.provider.name} (${vista.provider.id}).${cola}`;
}

/**
 * @param {{subprocessors?: {id: string, name: string}[], routing?: object[]}} vista
 * @param {number} nivel
 */
export function declaracionNivel(vista, nivel) {
  const fila = filaDe(vista, nivel);
  if (!fila) return "";
  const nombres = Object.fromEntries((vista.subprocessors ?? []).map((sub) => [sub.id, sub.name]));
  const modelos = fila.model_aliases.join(", ");
  const subs = (fila.subprocessors ?? [])
    .map((id) => (nombres[id] ? `${nombres[id]} (${id})` : id))
    .join(", ");
  const cola = subs ? ` Subencargados: ${subs}.` : "";
  return `La ruta es ${fila.hosting}. Modelos: ${modelos}.${cola}`;
}

/** @param {"installation"|"hosted"|string} hosting */
export function etiquetaHosting(hosting) {
  if (hosting === "installation") return "En la instalación";
  if (hosting === "hosted") return "Alojado por un subencargado";
  return hosting;
}

/** @param {"none"|"limited"|string} valor */
export function etiquetaRetencion(valor) {
  if (valor === "none") return "No conserva entradas ni salidas";
  if (valor === "limited") return "Conserva entradas o salidas";
  return valor;
}

/**
 * @param {{records: number, complete: boolean} | null | undefined} inventario
 * @returns {string}
 */
export function fraseInventario(inventario) {
  if (!inventario) return "";
  if (inventario.records === 0 && inventario.complete) {
    return "El proveedor no conserva radicados.";
  }
  const nombre = inventario.records === 1 ? "radicado" : "radicados";
  if (inventario.complete) return `El proveedor conserva ${inventario.records} ${nombre}.`;
  return `El proveedor conserva al menos ${inventario.records} ${nombre}.`;
}
