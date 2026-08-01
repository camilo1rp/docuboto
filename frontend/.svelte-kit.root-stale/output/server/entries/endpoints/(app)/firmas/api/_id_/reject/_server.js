import { j as json } from "../../../../../../../chunks/index.js";
import { A as API_V1 } from "../../../../../../../chunks/config.server.js";
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
  const { motivo } = body ?? {};
  if (!motivo || typeof motivo !== "string" || !motivo.trim()) {
    return json(
      {
        error: "validation_error",
        detail: "El motivo de rechazo es obligatorio."
      },
      { status: 422 }
    );
  }
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant
  };
  if (userId) headers["X-User-Id"] = userId;
  try {
    const res = await fetch(
      `${API_V1}/signature/requests/${encodeURIComponent(params.id)}/reject`,
      {
        method: "POST",
        headers,
        body: JSON.stringify({ motivo: motivo.trim() })
      }
    );
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de firmas." },
      { status: 503 }
    );
  }
}
export {
  POST
};
