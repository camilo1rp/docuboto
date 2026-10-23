// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * reserva.js — Clasificación y reserva (Ley 1712/2014 arts. 18-19), ADR-039.
 *
 * Espejo de las reglas del backend para que la pantalla no ofrezca lo que la
 * API rechazaría. La FUENTE es `GET /api/v1/documents/causales-reserva` (el
 * catálogo cerrado, el umbral de segunda persona y el plazo máximo): aquí solo
 * se filtra y se valida contra lo que ese endpoint entregó. El backend
 * re-valida siempre.
 *
 * Mapa nivel ↔ artículo: nivel 2 = reservada (art. 19, con plazo de 1 a 180
 * meses); nivel 3 = clasificada (art. 18, sin plazo).
 */

export const PERM_RECLASIFICAR = "PERM_RECLASIFICAR";

/** Nivel CRUD mínimo para consultar el historial de solicitudes de un radicado. */
export const CRUD_HISTORIAL = 1;
/** Solicitar y retirar una desclasificación (y reclasificar por PATCH). */
export const CRUD_SOLICITAR = 3;
/** Ver la cola, aprobar y rechazar: la segunda persona. */
export const CRUD_DECIDIR = 4;

/** Valor de respaldo si el catálogo no cargó; el servidor manda (`nivel_doble_firma`). */
export const NIVEL_DOBLE_FIRMA_DEFECTO = 3;
export const PLAZO_MAX_MESES_DEFECTO = 180;

/** Causal que escribe el sistema para lo clasificado sin motivar; la API la rechaza. */
export const POR_CLASIFICAR = "por_clasificar";
export const POR_CLASIFICAR_ETIQUETA = "Pendiente de motivar";

/**
 * @param {Record<string, number>|null|undefined} levels `permission_levels` del JWT
 * @param {number} minimo
 */
export function tieneNivelReclasificar(levels, minimo) {
  return (levels?.[PERM_RECLASIFICAR] ?? 0) >= minimo;
}

/** @param {Record<string, number>|null|undefined} levels */
export const puedeSolicitar = (levels) => tieneNivelReclasificar(levels, CRUD_SOLICITAR);
/** @param {Record<string, number>|null|undefined} levels */
export const puedeDecidir = (levels) => tieneNivelReclasificar(levels, CRUD_DECIDIR);

/**
 * ¿Bajar de `nivelActual` a `nivelDestino` exige solicitud y aprobación de otra
 * persona? Solo desde el umbral (nivel 3) y solo hacia abajo.
 *
 * @param {number} nivelActual
 * @param {number} nivelDestino
 * @param {number} [umbral]
 */
export function requiereSegundaPersona(nivelActual, nivelDestino, umbral) {
  const u = Number.isFinite(umbral) ? /** @type {number} */ (umbral) : NIVEL_DOBLE_FIRMA_DEFECTO;
  return Number(nivelActual) >= u && Number(nivelDestino) < Number(nivelActual);
}

/**
 * @typedef {{codigo: string, articulo: string, literal: string, descripcion: string,
 *   nivel: number, categoria?: string, plazo_max_meses: number|null}} CausalReserva
 */

/**
 * Etiqueta corta de un artículo: «Ley 1712/2014 art. 19» → «Art. 19».
 * @param {string|null|undefined} articulo
 */
export function articuloCorto(articulo) {
  const m = /art\.?\s*(\d+)/i.exec(articulo ?? "");
  return m ? `Art. ${m[1]}` : (articulo ?? "");
}

/**
 * Opciones de `<select>` de las causales que corresponden al NIVEL de destino
 * (nivel 3 → art. 18, nivel 2 → art. 19). Nivel 1 no lleva causal: lista vacía.
 *
 * @param {CausalReserva[]|null|undefined} catalogo
 * @param {number} nivel
 * @returns {Array<{value: string, label: string}>}
 */
export function causalesParaNivel(catalogo, nivel) {
  return (catalogo ?? [])
    .filter((c) => c && c.codigo && c.nivel === Number(nivel))
    .map((c) => ({
      value: c.codigo,
      label: `${articuloCorto(c.articulo)} lit. ${c.literal} — ${c.descripcion}`,
    }));
}

/**
 * @param {CausalReserva[]|null|undefined} catalogo
 * @param {string|null|undefined} codigo
 * @returns {CausalReserva|null}
 */
export function causalDe(catalogo, codigo) {
  return (catalogo ?? []).find((c) => c.codigo === codigo) ?? null;
}

/**
 * ¿La causal elegida lleva plazo? Solo el art. 19 (`plazo_max_meses` con valor);
 * el art. 18 se mantiene mientras subsista el derecho y NO lleva plazo.
 *
 * @param {CausalReserva|null} causal
 */
export function llevaPlazo(causal) {
  return causal?.plazo_max_meses != null;
}

/**
 * Valida en cliente la combinación nivel destino + causal + fundamento + plazo.
 * Devuelve los errores POR CAMPO (objeto vacío = válido). Es la misma regla del
 * backend (`validar_clasificacion`), escrita aquí solo para ahorrar el viaje.
 *
 * @param {{nivel: number, causal: string, fundamento: string, plazo: string,
 *   catalogo: CausalReserva[]|null|undefined, plazoMax?: number}} p
 * @returns {{causal?: string, fundamento?: string, plazo?: string}}
 */
export function validarClasificacion({ nivel, causal, fundamento, plazo, catalogo, plazoMax }) {
  /** @type {{causal?: string, fundamento?: string, plazo?: string}} */
  const errores = {};
  if (nivel < 2) return errores;
  const elegida = causalDe(catalogo, causal);
  if (!elegida || elegida.nivel !== nivel) {
    errores.causal = "Elija la causal de reserva del catálogo (Ley 1712 de 2014, arts. 18 y 19).";
  }
  if (!fundamento.trim()) {
    errores.fundamento =
      "El fundamento jurídico es obligatorio para reservar o clasificar (norma especial que sustenta la reserva).";
  }
  if (llevaPlazo(elegida)) {
    const tope = plazoMax ?? elegida?.plazo_max_meses ?? PLAZO_MAX_MESES_DEFECTO;
    const n = /^\d+$/.test(plazo.trim()) ? parseInt(plazo.trim(), 10) : NaN;
    if (!Number.isInteger(n) || n < 1 || n > tope) {
      errores.plazo = `El plazo de la reserva es obligatorio: un número entero de 1 a ${tope} meses.`;
    }
  }
  return errores;
}
