// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * assistant.service.js — Cliente del asistente conversacional (lado navegador).
 *
 * D-05: llama EXCLUSIVAMENTE al proxy SSR `/asistente/api/message`. El
 * access_token vive solo en `locals`/cookie httpOnly del servidor — este
 * módulo corre en el cliente y NUNCA ve el token ni contacta al api-gateway
 * directamente.
 *
 * `sendMessage()` lanza `AssistantError` con `.kind` para que el store
 * distinga "aún no disponible" (backend ausente/501/404, mcp-server E18
 * pendiente) de un error transitorio o de sesión — sin fabricar nunca una
 * respuesta simulada.
 */

/** @typedef {'unavailable'|'auth'|'invalid'|'network'} AssistantErrorKind */

export class AssistantError extends Error {
  /**
   * @param {AssistantErrorKind} kind
   * @param {string} message
   * @param {string|null} [detail]
   */
  constructor(kind, message, detail = null) {
    super(message);
    this.name = "AssistantError";
    this.kind = kind;
    this.detail = detail;
  }
}

/**
 * Confirma una propuesta que la persona aprobó en su tarjeta.
 *
 * El cuerpo lleva el token y —solo para los actos irreversibles— la frase
 * tecleada. Los argumentos del acto NO viajan: salen del token firmado en el
 * backend. Si se pudieran mandar desde aquí, la tarjeta describiría un acto y
 * se ejecutaría otro.
 *
 * @param {{ token: string, frase?: string }} params
 * @returns {Promise<any>}
 * @throws {AssistantError}
 */
export async function confirmarPropuesta({ token, frase = "" }) {
  let res;
  try {
    res = await fetch("/asistente/api/confirmar", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ token, ...(frase ? { frase } : {}) }),
    });
  } catch {
    throw new AssistantError("network", "No se pudo conectar con el asistente.");
  }

  /** @type {any} */
  let data = null;
  try {
    data = await res.json();
  } catch {
    // Sin cuerpo o cuerpo no-JSON — se resuelve abajo por status.
  }

  if (res.ok) return data;

  // El backend anida el error en `detail` (formato de error del proyecto).
  const cuerpo = data?.detail && typeof data.detail === "object" ? data.detail : data;
  const detail = cuerpo?.detail ?? null;

  if (res.status === 503) {
    throw new AssistantError("unavailable", "El asistente aún no está disponible.", detail);
  }
  if (res.status === 401) {
    throw new AssistantError("auth", "Sesión no válida. Inicie sesión de nuevo.", detail);
  }
  // 400 (falta la frase, propuesta malformada), 403 (le retiraron el permiso
  // entre proponer y confirmar), 409/410 (caducada o ya usada). El detalle del
  // backend es la explicación buena: se propaga tal cual en vez de sustituirlo
  // por un genérico que no dice qué hacer.
  throw new AssistantError("invalid", detail ?? "No se pudo llevar a cabo la acción.", detail);
}

/**
 * @typedef {object} AssistantTurnDTO
 * @property {string} id
 * @property {'assistant'} role
 * @property {string} texto
 * @property {string|null} html
 * @property {string} ts
 *
 * @typedef {object} PropuestaDTO
 * @property {string} id
 * @property {string} tool
 * @property {string} operacion
 * @property {{etiqueta: string, valor: string}[]} campos
 * @property {boolean} [irreversible]
 * @property {string|null} [frase_requerida]
 * @property {number} [expira_en]
 * @property {string} token
 *
 * @typedef {object} AssistantMessageResult
 * @property {string|null} conversation_id
 * @property {AssistantTurnDTO} message
 * @property {PropuestaDTO[]} [propuestas]
 */

/**
 * Envía un mensaje del usuario al asistente a través del proxy SSR.
 *
 * @param {{ mensaje: string, conversationId?: string|null }} params
 * @returns {Promise<AssistantMessageResult>}
 * @throws {AssistantError}
 */
export async function sendMessage({ mensaje, conversationId = null }) {
  let res;
  try {
    res = await fetch("/asistente/api/message", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        mensaje,
        ...(conversationId ? { conversation_id: conversationId } : {}),
      }),
    });
  } catch {
    throw new AssistantError("network", "No se pudo conectar con el asistente.");
  }

  /** @type {any} */
  let data = null;
  try {
    data = await res.json();
  } catch {
    // Sin cuerpo o cuerpo no-JSON — se resuelve abajo únicamente por status.
  }

  if (res.ok) {
    return data;
  }

  const detail = data?.detail ?? null;

  if (res.status === 503 || data?.error === "assistant_unavailable") {
    throw new AssistantError("unavailable", "El asistente aún no está disponible.", detail);
  }
  if (res.status === 401) {
    throw new AssistantError("auth", "Sesión no válida. Inicie sesión de nuevo.", detail);
  }
  if (res.status === 400) {
    throw new AssistantError("invalid", detail ?? "Mensaje inválido.", detail);
  }
  throw new AssistantError("network", detail ?? "Error al comunicarse con el asistente.", detail);
}
