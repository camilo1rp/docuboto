// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * assistant.js — Estado de la conversación con el asistente (lado cliente).
 *
 * El hilo vive SOLO en memoria (writable): no hay persistencia/historial
 * entre recargas — `reset()` empieza una conversación nueva (diferido
 * explícito de este slice, ver contrato §6).
 *
 * `send()` mantiene el turno del usuario en pantalla aunque el backend
 * falle: lo que el usuario escribió nunca se pierde. El estado
 * "unavailable" es honesto (backend ausente/501, mcp-server E18 pendiente):
 * nunca se fabrica una respuesta del asistente.
 */

import { writable, get } from "svelte/store";
import { sendMessage, AssistantError } from "$lib/services/assistant.service.js";

/**
 * @typedef {object} Turn
 * @property {string} id
 * @property {'user'|'assistant'} role
 * @property {string} texto
 * @property {string|null} html   HTML saneado server-side (solo turnos del asistente en markdown)
 * @property {string} ts
 *
 * @typedef {'idle'|'sending'|'error'|'unavailable'} AssistantEstado
 *
 * @typedef {object} AssistantState
 * @property {Turn[]} messages
 * @property {AssistantEstado} estado
 * @property {string|null} conversationId
 * @property {string|null} errorDetail
 */

/** @type {AssistantState} */
const initialState = {
  messages: [],
  estado: "idle",
  conversationId: null,
  errorDetail: null,
};

/** @type {import('svelte/store').Writable<AssistantState>} */
export const assistantState = writable(initialState);

let _seq = 0;
/** @param {string} prefix */
function nextId(prefix) {
  return `${prefix}-${Date.now()}-${++_seq}`;
}

/**
 * Envía un mensaje del usuario. Empuja el turno de usuario de forma
 * optimista (texto plano, `html:null`), marca `estado="sending"`, y según
 * el resultado del servicio:
 *  - éxito → empuja el turno del asistente, guarda `conversationId`, `estado="idle"`
 *  - `AssistantError.kind === "unavailable"` → `estado="unavailable"`
 *  - cualquier otro fallo → `estado="error"`, `errorDetail`
 *
 * @param {string} mensaje
 */
export async function send(mensaje) {
  const texto = (mensaje ?? "").trim();
  if (!texto) return;

  /** @type {Turn} */
  const userTurn = {
    id: nextId("u"),
    role: "user",
    texto,
    html: null,
    ts: new Date().toISOString(),
  };

  const conversationId = get(assistantState).conversationId;

  assistantState.update((s) => ({
    ...s,
    messages: [...s.messages, userTurn],
    estado: "sending",
    errorDetail: null,
  }));

  try {
    const result = await sendMessage({ mensaje: texto, conversationId });

    /** @type {Turn} */
    const assistantTurn = {
      id: result?.message?.id ?? nextId("a"),
      role: "assistant",
      texto: result?.message?.texto ?? "",
      html: result?.message?.html ?? null,
      ts: result?.message?.ts ?? new Date().toISOString(),
    };

    assistantState.update((s) => ({
      ...s,
      messages: [...s.messages, assistantTurn],
      estado: "idle",
      conversationId: result?.conversation_id ?? s.conversationId,
      errorDetail: null,
    }));
  } catch (err) {
    const kind = err instanceof AssistantError ? err.kind : "network";
    const detail = err instanceof AssistantError ? err.detail : null;
    assistantState.update((s) => ({
      ...s,
      estado: kind === "unavailable" ? "unavailable" : "error",
      errorDetail: detail ?? (err instanceof Error ? err.message : "Error desconocido."),
    }));
  }
}

/** Limpia la conversación (nuevo hilo). El historial solo vive en memoria. */
export function reset() {
  assistantState.set({ messages: [], estado: "idle", conversationId: null, errorDetail: null });
}
