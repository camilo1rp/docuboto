import { C as COOKIE_ACCESS_TOKEN } from "../../../../chunks/auth.server.js";
import { A as API_V1 } from "../../../../chunks/config.server.js";
async function load({ cookies, parent, url }) {
  const { user } = await parent();
  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";
  const userId = user?.sub ?? "";
  const q = url.searchParams.get("q") ?? "";
  const docType = url.searchParams.get("doc_type") ?? "";
  const status = url.searchParams.get("status") ?? "";
  const dateFrom = url.searchParams.get("date_from") ?? "";
  const dateTo = url.searchParams.get("date_to") ?? "";
  const rawDeptCode = url.searchParams.get("dest_dept_code") ?? "";
  const destDeptCode = rawDeptCode !== "" && /^\d+$/.test(rawDeptCode.trim()) ? String(Math.floor(Number(rawDeptCode))) : "";
  const page = Math.max(1, Number(url.searchParams.get("page") ?? 1));
  const size = 20;
  let results = [];
  let total = 0;
  let loadError = null;
  if (q.length >= 2) {
    const params = new URLSearchParams({ q, page: String(page), size: String(size) });
    if (docType) params.set("doc_type", docType);
    if (status) params.set("status", status);
    if (dateFrom) params.set("date_from", dateFrom);
    if (dateTo) params.set("date_to", dateTo);
    if (destDeptCode) params.set("dest_dept_code", destDeptCode);
    try {
      const res = await fetch(`${API_V1}/search?${params.toString()}`, {
        headers: {
          Authorization: `Bearer ${token}`,
          "X-Tenant-Slug": tenant,
          "X-User-Id": userId
        }
      });
      if (res.ok) {
        const data = await res.json();
        if (Array.isArray(data)) {
          results = data;
          total = Number(res.headers.get("X-Total-Count") ?? data.length);
        } else {
          results = data.items ?? data.results ?? [];
          total = data.total ?? Number(res.headers.get("X-Total-Count") ?? 0);
        }
      } else {
        const body = await res.json().catch(() => ({}));
        loadError = body.detail ?? `Error ${res.status}`;
      }
    } catch {
      loadError = "No se pudo conectar al servidor de búsqueda.";
    }
  }
  return {
    results,
    total,
    loadError,
    // Filtros activos para inicializar el formulario en el cliente
    initialFilters: { q, docType, status, dateFrom, dateTo, destDeptCode, page }
    // token y user NO se exponen: las llamadas de paginación usan /busqueda/api (D-05)
  };
}
export {
  load
};
