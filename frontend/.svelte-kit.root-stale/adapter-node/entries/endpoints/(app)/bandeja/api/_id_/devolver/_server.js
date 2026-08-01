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
  const { to_dept, causal, comentario } = body ?? {};
  if (!to_dept || typeof to_dept !== "string" || !to_dept.trim()) {
    return json(
      {
        error: "validation_error",
        detail: "El campo 'to_dept' (dependencia destino) es obligatorio."
      },
      { status: 422 }
    );
  }
  if (to_dept.trim().length > 100) {
    return json(
      { error: "validation_error", detail: "El campo 'to_dept' no puede superar 100 caracteres." },
      { status: 422 }
    );
  }
  if (!causal || typeof causal !== "string" || !causal.trim()) {
    return json(
      { error: "validation_error", detail: "El campo 'causal' es obligatorio." },
      { status: 422 }
    );
  }
  if (causal.trim().length > 15) {
    return json(
      { error: "validation_error", detail: "El campo 'causal' no puede superar 15 caracteres." },
      { status: 422 }
    );
  }
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant
  };
  if (userId) headers["X-User-Id"] = userId;
  const payload = {
    to_dept: to_dept.trim(),
    causal: causal.trim()
  };
  if (comentario && typeof comentario === "string" && comentario.trim()) {
    payload.comentario = comentario.trim().slice(0, 2e3);
  }
  try {
    const res = await fetch(`${API_V1}/workflows/${id}/devolver`, {
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
      { error: "connection_error", detail: "No se pudo devolver el radicado." },
      { status: 503 }
    );
  }
}
export {
  POST
};
