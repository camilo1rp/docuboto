import { C as COOKIE_ACCESS_TOKEN } from "../../../../chunks/auth.server.js";
import { A as API_V1 } from "../../../../chunks/config.server.js";
async function load({ cookies, parent, url }) {
  const { user } = await parent();
  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";
  const estado = url.searchParams.get("estado") ?? "";
  const page = Math.max(1, Number(url.searchParams.get("page") ?? 1));
  const size = 20;
  const params = new URLSearchParams({ page: String(page), size: String(size) });
  if (estado) params.set("estado", estado);
  let shipments = [];
  let total = 0;
  let loadError = null;
  try {
    const res = await fetch(`${API_V1}/envios?${params.toString()}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant
      }
    });
    if (res.ok) {
      total = Number(res.headers.get("X-Total-Count") ?? 0);
      const data = await res.json();
      if (Array.isArray(data)) shipments = data;
    } else {
      const body = await res.json().catch(() => ({}));
      loadError = body.detail ?? `Error ${res.status}`;
    }
  } catch {
    loadError = "No se pudo conectar al servidor de envíos.";
  }
  return {
    shipments,
    total,
    loadError,
    initialFilters: { estado, page }
    // token y tenant NO se exponen: las llamadas cliente usan /envios/api (D-05)
  };
}
export {
  load
};
