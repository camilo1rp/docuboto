import { j as json } from "../../../../../../../chunks/index.js";
import { A as API_V1 } from "../../../../../../../chunks/config.server.js";
async function POST({ locals, params, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  const { id } = params;
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }
  const { aprobar, comentario } = body ?? {};
  if (typeof aprobar !== "boolean") {
    return json(
      { error: "validation_error", detail: "El campo 'aprobar' es obligatorio (boolean)." },
      { status: 422 }
    );
  }
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant
  };
  if (userId) headers["X-User-Id"] = userId;
  const payload = { aprobar };
  if (comentario && typeof comentario === "string" && comentario.trim()) {
    payload.comentario = comentario.trim().slice(0, 2e3);
  }
  try {
    const res = await fetch(`${API_V1}/workflows/${id}/vistos-buenos/decidir`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload)
    });
    if (res.status === 204) {
      return json({ ok: true }, { status: 200 });
    }
    const data = await res.json().catch(() => ({ ok: true }));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo registrar la decisión del visto bueno." },
      { status: 503 }
    );
  }
}
export {
  POST
};
