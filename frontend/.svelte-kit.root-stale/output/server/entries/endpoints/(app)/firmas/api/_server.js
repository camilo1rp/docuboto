import { j as json } from "../../../../../chunks/index.js";
import { A as API_V1 } from "../../../../../chunks/config.server.js";
async function GET({ locals, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const page = url.searchParams.get("page") ?? "1";
  const size = url.searchParams.get("size") ?? "20";
  const params = new URLSearchParams({ page, size });
  try {
    const res = await fetch(`${API_V1}/signature/pending?${params}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant
      }
    });
    const headerTotal = res.headers.get("X-Total-Count");
    const data = await res.json().catch(() => ({}));
    if (!res.ok) return json(data, { status: res.status });
    const items = Array.isArray(data?.items) ? data.items : [];
    const total = headerTotal ? Number(headerTotal) : data?.total ?? items.length;
    return json({ pendientes: items, total });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de firmas." },
      { status: 503 }
    );
  }
}
export {
  GET
};
