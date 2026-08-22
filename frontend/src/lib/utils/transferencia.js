// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Estados de una transferencia documental (E12).
 *
 * Fuente única, y existe por un defecto que dejó el módulo entero inoperable:
 * la pantalla comparaba contra `"pendiente"`, un estado **que no existe**. El
 * ciclo real es `preparada → enviada → recibida | rechazada`, y así está en el
 * servicio (`_VALID`), en el docstring del módulo y en el `CHECK` de la tabla.
 *
 * Consecuencia medida en el navegador: una transferencia recién creada nace
 * `preparada`, así que **ninguno de los botones aparecía nunca** — ni Enviar,
 * ni Rechazar, ni después Recibir. No había forma de mover una transferencia
 * desde la interfaz, y el fallo es mudo: la fila se pinta, el estado se ve, y
 * lo único que falta son unas acciones cuya ausencia se lee como «aquí no
 * toca». El estado inventado estaba además en el filtro del listado y en la
 * allowlist del proxy, que rechazaba `preparada` — el filtro que sí servía.
 *
 * Un guardián (`transferencia-estados.test.js`) comprueba que esta lista
 * coincide con la del servicio: se deriva del backend, no se copia de memoria.
 */

/** Estados canónicos, en orden del ciclo de vida. */
export const ESTADOS_TRANSFERENCIA = ["preparada", "enviada", "recibida", "rechazada"];

/** Etiqueta y color por estado. */
export const ESTADO_TRANSFERENCIA_MAP = {
  preparada: { label: "Preparada", status: "warning" },
  enviada: { label: "Enviada", status: "info" },
  recibida: { label: "Recibida", status: "success" },
  rechazada: { label: "Rechazada", status: "danger" },
};

/**
 * Transiciones permitidas, espejo de `_VALID` en `transferencia_service.py`.
 * La UI las usa para decidir qué acciones ofrecer; el backend re-valida.
 */
export const TRANSICIONES_TRANSFERENCIA = {
  preparada: ["enviada"],
  enviada: ["recibida", "rechazada"],
  recibida: [],
  rechazada: [],
};

/**
 * ¿Se puede pasar de `estado` a `destino`?
 * @param {string} estado
 * @param {string} destino
 * @returns {boolean}
 */
export function admiteTransicion(estado, destino) {
  return (TRANSICIONES_TRANSFERENCIA[estado] ?? []).includes(destino);
}
