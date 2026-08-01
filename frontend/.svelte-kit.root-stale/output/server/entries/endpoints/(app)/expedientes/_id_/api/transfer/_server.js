import { j as json } from "../../../../../../../chunks/index.js";
import { A as API_V1 } from "../../../../../../../chunks/config.server.js";
async function POST({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const { id } = params;
  try {
    const res = await fetch(`${API_V1}/expedientes/${id}/transfer`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant
      }
    });
    if (res.status === 204) {
      return json({ ok: true }, { status: 200 });
    }
    const data = await res.json().catch(() => ({ ok: true }));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo transferir el expediente." },
      { status: 503 }
    );
  }
}
export {
  POST
};
