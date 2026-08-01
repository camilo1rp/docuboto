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
  const { causal, motivo } = body ?? {};
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
  const payload = { causal: causal.trim() };
  if (motivo && typeof motivo === "string" && motivo.trim()) {
    payload.motivo = motivo.trim().slice(0, 2e3);
  }
  try {
    const res = await fetch(`${API_V1}/documents/${id}/anulacion`, {
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
      { error: "connection_error", detail: "No se pudo solicitar la anulación." },
      { status: 503 }
    );
  }
}
export {
  POST
};
