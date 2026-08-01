import { C as COOKIE_ACCESS_TOKEN } from "../../../../chunks/auth.server.js";
import { A as API_V1 } from "../../../../chunks/config.server.js";
import { b as attachSafeBody } from "../../../../chunks/sanitizeHtml.js";
async function load({ cookies, parent, url, fetch }) {
  const { user } = await parent();
  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";
  const docType = url.searchParams.get("doc_type") ?? "";
  const status = url.searchParams.get("status") ?? "";
  const page = Math.max(1, Number(url.searchParams.get("page") ?? 1));
  const size = 20;
  const params = new URLSearchParams({ page: String(page), size: String(size) });
  if (docType) params.set("doc_type", docType);
  if (status) params.set("status", status);
  let documents = [];
  let total = 0;
  let loadError = null;
  try {
    const res = await fetch(`${API_V1}/documents?${params.toString()}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant
      }
    });
    if (res.ok) {
      total = Number(res.headers.get("X-Total-Count") ?? 0);
      documents = await res.json();
      if (Array.isArray(documents)) documents.forEach(attachSafeBody);
    } else {
      const body = await res.json().catch(() => ({}));
      loadError = body.detail ?? `Error ${res.status}`;
    }
  } catch {
    loadError = "No se pudo conectar al servidor de documentos.";
  }
  return {
    documents,
    total,
    loadError,
    // Filtros activos al cargar (para deep-link)
    initialFilters: { docType, status, page }
    // token y tenant NO se exponen: las llamadas cliente usan /bandeja/api (D-05)
  };
}
export {
  load
};
