import { j as json } from "../../../../../../chunks/index.js";
import { marked } from "marked";
import { A as API_V1 } from "../../../../../../chunks/config.server.js";
import { s as sanitizeHtml } from "../../../../../../chunks/sanitizeHtml.js";
const MAX_MENSAJE_LEN = 4e3;
function isUnavailableStatus(status) {
  return status === 404 || status === 501 || status === 502 || status === 503 || status === 504;
}
function unavailableResponse() {
  return json(
    { error: "assistant_unavailable", detail: "El asistente aún no está disponible." },
    { status: 503 }
  );
}
async function POST({ locals, request }) {
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
  const payload = {};
  if (typeof mensaje === "string" && mensaje.trim()) {
    payload.mensaje = mensaje.trim().slice(0, MAX_MENSAJE_LEN);
  }
  if (!payload.mensaje) {
    return json(
      { error: "empty_message", detail: "El mensaje no puede estar vacío." },
      { status: 400 }
    );
  }
  if (typeof conversation_id === "string" && conversation_id.trim()) {
    payload.conversation_id = conversation_id.trim();
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  const username = locals.user?.preferred_username ?? "";
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant
  };
  if (userId) headers["X-User-Id"] = userId;
  if (username) headers["X-Username"] = username;
  let res;
  try {
    res = await fetch(`${API_V1}/assistant/message`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload)
    });
  } catch {
    return unavailableResponse();
  }
  if (isUnavailableStatus(res.status)) {
    return unavailableResponse();
  }
  if (res.status === 401) {
    const data2 = await res.json().catch(() => ({ error: "unauthenticated" }));
    return json(data2, { status: 401 });
  }
  if (!res.ok) {
    const data2 = await res.json().catch(() => ({ error: "assistant_error" }));
    return json(data2, { status: res.status });
  }
  let data;
  try {
    data = await res.json();
  } catch {
    return unavailableResponse();
  }
  const msg = data?.message ?? {};
  const texto = typeof msg.texto === "string" ? msg.texto : "";
  const formato = msg.formato === "markdown" ? "markdown" : "text";
  const html = formato === "markdown" && texto ? sanitizeHtml(marked.parse(texto)) : null;
  return json(
    {
      conversation_id: typeof data?.conversation_id === "string" ? data.conversation_id : null,
      message: {
        id: typeof msg.id === "string" && msg.id ? msg.id : void 0,
        role: "assistant",
        texto,
        html,
        ts: typeof msg.ts === "string" && msg.ts ? msg.ts : (/* @__PURE__ */ new Date()).toISOString()
      }
    },
    { status: 200 }
  );
}
export {
  POST
};
