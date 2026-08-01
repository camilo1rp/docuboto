import { j as json } from "../../../../../chunks/index.js";
import { A as API_V1 } from "../../../../../chunks/config.server.js";
import { b as attachSafeBody } from "../../../../../chunks/sanitizeHtml.js";
async function GET({ locals, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const docType = url.searchParams.get("doc_type") ?? "";
  const status = url.searchParams.get("status") ?? "";
  const page = url.searchParams.get("page") ?? "1";
  const size = url.searchParams.get("size") ?? "20";
  const params = new URLSearchParams({ page, size });
  if (docType) params.set("doc_type", docType);
  if (status) params.set("status", status);
  try {
    const res = await fetch(`${API_V1}/documents?${params}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant
      }
    });
    const total = Number(res.headers.get("X-Total-Count") ?? 0);
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      return json(data, { status: res.status });
    }
    if (Array.isArray(data)) data.forEach(attachSafeBody);
    return json({ documents: data, total });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de documentos." },
      { status: 503 }
    );
  }
}
export {
  GET
};
