import { j as json } from "../../../../../../../chunks/index.js";
import { A as API_V1 } from "../../../../../../../chunks/config.server.js";
async function POST({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant
  };
  if (userId) headers["X-User-Id"] = userId;
  try {
    const res = await fetch(`${API_V1}/signature/requests/${encodeURIComponent(params.id)}/sign`, {
      method: "POST",
      headers,
      body: JSON.stringify({})
    });
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
