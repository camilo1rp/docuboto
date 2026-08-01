import { j as json } from "../../../../../../../chunks/index.js";
import { A as API_V1 } from "../../../../../../../chunks/config.server.js";
const ESTADOS_VALIDOS = /* @__PURE__ */ new Set([
  "registrado",
  "en_transito",
  "entregado",
  "devuelto",
  "fallido"
]);
async function POST({ locals, params, request }) {
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
  const { estado, descripcion } = body ?? {};
  if (!estado || !ESTADOS_VALIDOS.has(estado)) {
    return json(
      {
        error: "validation_error",
        detail: "Estado no válido. Use: registrado, en_transito, entregado, devuelto o fallido."
      },
      { status: 422 }
    );
  }
  const payload = {
    estado,
    descripcion: typeof descripcion === "string" && descripcion.trim() ? descripcion.trim() : null
  };
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant
  };
  if (userId) headers["X-User-Id"] = userId;
  try {
    const res = await fetch(`${API_V1}/envios/${encodeURIComponent(params.id)}/estado`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload)
    });
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
