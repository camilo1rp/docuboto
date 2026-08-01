import { C as COOKIE_ACCESS_TOKEN } from "../../../../chunks/auth.server.js";
import { A as API_V1 } from "../../../../chunks/config.server.js";
async function load({ cookies, parent, url }) {
  const { user } = await parent();
  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";
  const userId = user?.sub ?? "";
  const dateFrom = url.searchParams.get("date_from") ?? "";
  const dateTo = url.searchParams.get("date_to") ?? "";
  const params = new URLSearchParams();
  if (dateFrom) params.set("date_from", dateFrom);
  if (dateTo) params.set("date_to", dateTo);
  const qs = params.toString();
  let summary = null;
  let loadError = null;
  try {
    const res = await fetch(`${API_V1}/reports/radicados${qs ? `?${qs}` : ""}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
        "X-User-Id": userId
      }
    });
    if (res.ok) {
      summary = await res.json();
    } else {
      const body = await res.json().catch(() => ({}));
      loadError = body.detail ?? `Error ${res.status}`;
    }
  } catch {
    loadError = "No se pudo conectar al servidor de reportes.";
  }
  return {
    summary,
    loadError,
    // Filtros activos para inicializar el formulario y construir la URL del CSV
    initialFilters: { dateFrom, dateTo }
    // token NO se expone: el botón CSV llama a /reportes/api/csv (D-05)
  };
}
export {
  load
};
