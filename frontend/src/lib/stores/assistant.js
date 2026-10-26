// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
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
import {
  sendMessage,
  confirmarPropuesta,
  AssistantError,
} from "$lib/services/assistant.service.js";

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
 * @typedef {object} Propuesta
 * @property {string} id
 * @property {string} tool
 * @property {string} operacion
 * @property {{etiqueta: string, valor: string}[]} campos
 * @property {boolean} [irreversible]
 * @property {string|null} [frase_requerida]
 * @property {string} token
 * @property {'pendiente'|'ejecutando'|'hecho'|'cancelado'|'error'} estado
 * @property {string} [detalleError]
 *
 * @typedef {object} AssistantState
 * @property {Turn[]} messages
 * @property {AssistantEstado} estado
 * @property {Propuesta[]} propuestas
 * @property {string|null} conversationId
 * @property {string|null} errorDetail
 */

/** @type {AssistantState} */
const initialState = {
  messages: [],
  estado: "idle",
  propuestas: [],
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

    // Las propuestas del turno se AÑADEN a las que ya hubiera, no las
    // sustituyen: una anterior que la persona no haya resuelto sigue siendo un
    // acto pendiente de su decisión, y hacerla desaparecer porque llegó otro
    // mensaje sería decidir por ella.
    const nuevas = (result?.propuestas ?? []).map(
      (p) => /** @type {Propuesta} */ ({ ...p, estado: "pendiente" }),
    );

    assistantState.update((s) => ({
      ...s,
      messages: [...s.messages, assistantTurn],
      estado: "idle",
      propuestas: [...s.propuestas, ...nuevas],
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

/**
 * Confirma una propuesta que la persona aprobó en su tarjeta.
 *
 * Marca SOLO esa propuesta, por `id`: una confirmación por acto, también dentro
 * de una cadena. Un botón que aprobara «todo lo pendiente» sería el booleano
 * global que este diseño retiró, con otra forma.
 *
 * @param {string} id
 * @param {string} [frase]
 */
export async function confirmar(id, frase = "") {
  const actual = get(assistantState).propuestas.find((p) => p.id === id);
  if (!actual || actual.estado !== "pendiente") return;

  marcar(id, { estado: "ejecutando", detalleError: "" });
  try {
    await confirmarPropuesta({ token: actual.token, frase });
    marcar(id, { estado: "hecho" });
  } catch (err) {
    // Se queda en 'error' y NO vuelve a 'pendiente' con el mismo token: si la
    // propuesta caducó o ya se consumió, reintentar con ella no puede funcionar,
    // y ofrecer un botón que siempre falla es peor que decir qué pasó.
    const detalle = err instanceof AssistantError ? err.detail : null;
    marcar(id, {
      estado: "error",
      detalleError: detalle ?? "No se pudo llevar a cabo la acción.",
    });
  }
}

/** Descarta una propuesta sin ejecutarla. No se avisa a nadie: no ocurrió nada. */
export function descartar(id) {
  marcar(id, { estado: "cancelado" });
}

/**
 * @param {string} id
 * @param {Partial<Propuesta>} cambios
 */
function marcar(id, cambios) {
  assistantState.update((s) => ({
    ...s,
    propuestas: s.propuestas.map((p) => (p.id === id ? { ...p, ...cambios } : p)),
  }));
}

/** Limpia la conversación (nuevo hilo). El historial solo vive en memoria. */
export function reset() {
  assistantState.set({
    messages: [],
    estado: "idle",
    propuestas: [],
    conversationId: null,
    errorDetail: null,
  });
}
