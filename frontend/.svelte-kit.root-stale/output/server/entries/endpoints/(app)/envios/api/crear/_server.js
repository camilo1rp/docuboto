import { j as json } from "../../../../../../chunks/index.js";
import { A as API_V1 } from "../../../../../../chunks/config.server.js";
const OPERADORES_VALIDOS = /* @__PURE__ */ new Set(["4-72", "servientrega", "otro"]);
async function POST({ locals, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }
  const { radicado_id, operador, destinatario, direccion } = body ?? {};
  if (!radicado_id || typeof radicado_id !== "string" || !radicado_id.trim()) {
    return json(
      { error: "validation_error", detail: "El campo radicado_id es obligatorio." },
      { status: 422 }
    );
  }
  if (!operador || !OPERADORES_VALIDOS.has(operador)) {
    return json(
      { error: "validation_error", detail: "Operador no válido. Use: 4-72, servientrega u otro." },
      { status: 422 }
    );
  }
  const payload = {
    operador,
    destinatario: typeof destinatario === "string" && destinatario.trim() ? destinatario.trim() : null,
    direccion: typeof direccion === "string" && direccion.trim() ? direccion.trim() : null
  };
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant
  };
  if (userId) headers["X-User-Id"] = userId;
  try {
    const res = await fetch(
      `${API_V1}/documents/${encodeURIComponent(radicado_id.trim())}/envios`,
      {
        method: "POST",
        headers,
        body: JSON.stringify(payload)
      }
    );
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de envíos." },
      { status: 503 }
    );
  }
}
export {
  POST
};
