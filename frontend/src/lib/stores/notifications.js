// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * notifications.js — Cola de toasts/notificaciones en pantalla.
 *
 * API:
 *   notify({ type, message, duration? })  → añade un toast
 *   dismiss(id)                            → elimina el toast por id
 *
 * El componente Toast.svelte escucha este store y renderiza la cola.
 * Los toasts se eliminan solos al expirar `duration` (default 4 s).
 *
 * Tipos: 'success' | 'error' | 'warning' | 'info'
 */

import { writable } from "svelte/store";

/**
 * @typedef {'success'|'error'|'warning'|'info'} ToastType
 *
 * @typedef {object} Toast
 * @property {string}    id
 * @property {ToastType} type
 * @property {string}    message
 * @property {number}    duration   ms hasta auto-dismiss (0 = permanente)
 */

/** @type {import('svelte/store').Writable<Toast[]>} */
export const toasts = writable([]);

let _seq = 0;

/**
 * Añade un toast a la cola.
 *
 * @param {{ type?: ToastType; message: string; duration?: number }} opts
 * @returns {string} id del toast creado
 */
export function notify({ type = "info", message, duration = 4000 }) {
  const id = `toast-${++_seq}`;
  const toast = { id, type, message, duration };

  toasts.update((list) => [...list, toast]);

  if (duration > 0) {
    setTimeout(() => dismiss(id), duration);
  }

  return id;
}

/** @param {string} id */
export function dismiss(id) {
  toasts.update((list) => list.filter((t) => t.id !== id));
}

// Atajos semánticos
/** @param {string} message @param {number} [duration] */
export const notifySuccess = (message, duration) => notify({ type: "success", message, duration });

/** @param {string} message @param {number} [duration] */
export const notifyError = (message, duration) =>
  notify({ type: "error", message, duration: duration ?? 6000 });

/** @param {string} message @param {number} [duration] */
export const notifyWarning = (message, duration) => notify({ type: "warning", message, duration });

/** @param {string} message @param {number} [duration] */
export const notifyInfo = (message, duration) => notify({ type: "info", message, duration });
