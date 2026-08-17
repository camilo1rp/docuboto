// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /asistente/api/message — Proxy SSR: turno del asistente conversacional (D-05, E22/E18).
 *
 * El token y los claims se leen SOLO de `event.locals` (cookie httpOnly +
 * JWT verificado por hooks.server.js). El JS del cliente nunca ve el token
 * ni llama al api-gateway directamente — llama a este proxy.
 *
 * `POST /api/v1/assistant/message` en el gateway (mcp-server, E18) YA EXISTE
 * — el comentario que decía lo contrario sobrevivió a su propia premisa. La
 * degradación se mantiene igual, porque sigue siendo lo correcto cuando el
 * servicio está caído o sin desplegar: cualquier fallo de conexión, 404 o
 * 5xx/501 se traduce en un `503 assistant_unavailable`, un estado HONESTO para
 * la UI, nunca una respuesta simulada. 401 se propaga (sesión inválida); 400
 * se propaga (validación del backend).
 *
 * Autorización: gating por "autenticado", sin permiso `--op` fino (ver
 * contrato §5) — cada acción que el asistente dispare hacia adelante se
 * re-valida contra su propio RBAC/clearance en el backend real que la
 * ejecute; este proxy es solo el transporte del turno de chat.
 *
 * Allowlist campo a campo (anti mass-assignment): `mensaje` (string, trim,
 * máx. 4000) y `conversation_id` (string, opcional). Nunca se reenvía el
 * body íntegro del cliente.
 *
 * Salida — contenido NO CONFIABLE (posible XSS/prompt-injection renderizada):
 * si el gateway responde `formato:"markdown"`, este proxy convierte el
 * `texto` a HTML con `marked` y lo SANEA server-side con
 * `$lib/server/sanitizeHtml.js` ANTES de que cruce al cliente. El cliente
 * solo hace `{@html}` del campo `html` ya saneado; si es `null` renderiza
 * `texto` como texto plano (escapado por Svelte).
 *
 * POST → { mensaje: string, conversation_id?: string }
 *   Respuesta 200: { conversation_id, message: {...}, propuestas: Propuesta[] }
 *   Respuesta 401/400/503: { error, detail, status }
 *
 * PROPUESTAS (bloque 10): una acción de escritura no se ejecuta — el backend
 * devuelve un acto descrito y firmado que la persona aprueba en su tarjeta. Se
 * reenvían campo a campo, igual que la entrada: los VALORES los compuso el
 * modelo a partir de la conversación (que puede incluir el contenido de un
 * radicado de Entrada, es decir, texto de un tercero), así que viajan como
 * datos y se pintan como texto — nunca como HTML, ni siquiera saneado. Del
 * `texto` del asistente sí se hace `{@html}`; de la tarjeta, jamás: es
 * precisamente el sitio donde un texto engañoso serviría para que alguien
 * apruebe una cosa creyendo aprobar otra.
 */

import { json } from "@sveltejs/kit";
import { marked } from "marked";
import { API_V1 } from "$lib/utils/config.server.js";
import { sanitizeHtml } from "$lib/server/sanitizeHtml.js";

const MAX_MENSAJE_LEN = 4000;
/** Tope defensivo: una respuesta con cientos de tarjetas no es confirmable. */
const MAX_PROPUESTAS = 10;
const MAX_CAMPOS = 30;
const MAX_VALOR_LEN = 2000;

/** Un valor de tarjeta, reducido a algo que se pueda LEER antes de firmar. */
function valorLegible(v) {
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v.slice(0, MAX_VALOR_LEN);
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  // Objetos y listas (p. ej. `metadata`): se serializan acotados en vez de
  // dejar que el componente los pinte como "[object Object]", que no le dice
  // nada a quien tiene que aprobar el acto.
  try {
    return JSON.stringify(v).slice(0, MAX_VALOR_LEN);
  } catch {
    return "";
  }
}

/** Allowlist campo a campo de las propuestas que devuelve el backend. */
function propuestasSeguras(raw) {
  if (!Array.isArray(raw)) return [];
  return raw.slice(0, MAX_PROPUESTAS).flatMap((p) => {
    if (!p || typeof p.token !== "string" || !p.token) return [];
    const campos = Array.isArray(p.campos) ? p.campos.slice(0, MAX_CAMPOS) : [];
    return [
      {
        id: typeof p.id === "string" ? p.id : "",
        tool: typeof p.tool === "string" ? p.tool : "",
        operacion: typeof p.operacion === "string" ? p.operacion : "",
        campos: campos.map((c) => ({
          etiqueta: typeof c?.etiqueta === "string" ? c.etiqueta : String(c?.arg ?? ""),
          valor: valorLegible(c?.valor),
        })),
        expira_en: Number.isFinite(p.expira_en) ? p.expira_en : 0,
        token: p.token,
      },
    ];
  });
}

/** Ausencia/degradación del backend E18 → estado honesto "no disponible", nunca una respuesta fabricada. */
function isUnavailableStatus(status) {
  return status === 404 || status === 501 || status === 502 || status === 503 || status === 504;
}

function unavailableResponse() {
  return json(
    { error: "assistant_unavailable", detail: "El asistente aún no está disponible." },
    { status: 503 },
  );
}

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  const { mensaje, conversation_id } = body ?? {};

  // Allowlist campo a campo — nunca reenviar JSON.stringify(body) íntegro (anti mass-assignment).
  /** @type {Record<string, unknown>} */
  const payload = {};
  if (typeof mensaje === "string" && mensaje.trim()) {
    payload.mensaje = mensaje.trim().slice(0, MAX_MENSAJE_LEN);
  }
  if (!payload.mensaje) {
    return json(
      { error: "empty_message", detail: "El mensaje no puede estar vacío." },
      { status: 400 },
    );
  }
  if (typeof conversation_id === "string" && conversation_id.trim()) {
    payload.conversation_id = conversation_id.trim();
  }

  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  const username = locals.user?.preferred_username ?? "";

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;
  if (username) headers["X-Username"] = username;

  let res;
  try {
    res = await fetch(`${API_V1}/assistant/message`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });
  } catch {
    // Gateway/mcp-server inalcanzable (ECONNREFUSED/DNS) — ruta E18 aún no desplegada.
    return unavailableResponse();
  }

  if (isUnavailableStatus(res.status)) {
    return unavailableResponse();
  }

  if (res.status === 401) {
    const data = await res.json().catch(() => ({ error: "unauthenticated" }));
    return json(data, { status: 401 });
  }

  if (!res.ok) {
    const data = await res.json().catch(() => ({ error: "assistant_error" }));
    return json(data, { status: res.status });
  }

  /** @type {any} */
  let data;
  try {
    data = await res.json();
  } catch {
    return unavailableResponse();
  }

  const msg = data?.message ?? {};
  const texto = typeof msg.texto === "string" ? msg.texto : "";
  const formato = msg.formato === "markdown" ? "markdown" : "text";
  // Contenido NO CONFIABLE (posible XSS/prompt-injection renderizada): el
  // markdown se convierte a HTML y se sanea SIEMPRE server-side antes de
  // cruzar al cliente. `formato !== "markdown"` → sin HTML (texto plano).
  const html = formato === "markdown" && texto ? sanitizeHtml(marked.parse(texto)) : null;

  return json(
    {
      conversation_id: typeof data?.conversation_id === "string" ? data.conversation_id : null,
      propuestas: propuestasSeguras(data?.propuestas),
      message: {
        id: typeof msg.id === "string" && msg.id ? msg.id : undefined,
        role: "assistant",
        texto,
        html,
        ts: typeof msg.ts === "string" && msg.ts ? msg.ts : new Date().toISOString(),
      },
    },
    { status: 200 },
  );
}
