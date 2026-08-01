import { j as json } from "../../../../../chunks/index.js";
import { A as API_V1 } from "../../../../../chunks/config.server.js";
async function GET({ locals, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const estado = url.searchParams.get("estado") ?? "";
  const page = url.searchParams.get("page") ?? "1";
  const size = url.searchParams.get("size") ?? "20";
  const params = new URLSearchParams({ page, size });
  if (estado) params.set("estado", estado);
  try {
    const res = await fetch(`${API_V1}/envios?${params}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant
      }
    });
    const total = Number(res.headers.get("X-Total-Count") ?? 0);
    const data = await res.json().catch(() => []);
    if (!res.ok) return json(data, { status: res.status });
    return json({ shipments: Array.isArray(data) ? data : [], total });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de envíos." },
      { status: 503 }
    );
  }
}
export {
  GET
};
