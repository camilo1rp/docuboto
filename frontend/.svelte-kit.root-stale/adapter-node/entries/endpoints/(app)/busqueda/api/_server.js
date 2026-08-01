import { j as json } from "../../../../../chunks/index.js";
import { A as API_V1 } from "../../../../../chunks/config.server.js";
async function GET({ locals, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  const q = url.searchParams.get("q") ?? "";
  const docType = url.searchParams.get("doc_type") ?? "";
  const status = url.searchParams.get("status") ?? "";
  const dateFrom = url.searchParams.get("date_from") ?? "";
  const dateTo = url.searchParams.get("date_to") ?? "";
  const rawDeptCode = url.searchParams.get("dest_dept_code") ?? "";
  const destDeptCodeNum = rawDeptCode !== "" && /^\d+$/.test(rawDeptCode.trim()) ? Math.floor(Number(rawDeptCode)) : null;
  const page = url.searchParams.get("page") ?? "1";
  const rawSize = url.searchParams.get("size") ?? "20";
  const size = String(Math.min(100, Math.max(1, Number(rawSize) || 20)));
  if (q.length < 2) {
    return json(
      { error: "validation_error", detail: "La búsqueda requiere al menos 2 caracteres." },
      { status: 400 }
    );
  }
  const params = new URLSearchParams({ q, page, size });
  if (docType) params.set("doc_type", docType);
  if (status) params.set("status", status);
  if (dateFrom) params.set("date_from", dateFrom);
  if (dateTo) params.set("date_to", dateTo);
  if (destDeptCodeNum !== null) params.set("dest_dept_code", String(destDeptCodeNum));
  try {
    const res = await fetch(`${API_V1}/search?${params.toString()}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
        "X-User-Id": userId
      }
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      return json(data, { status: res.status });
    }
    let results, total;
    if (Array.isArray(data)) {
      results = data;
      total = Number(res.headers.get("X-Total-Count") ?? data.length);
    } else {
      results = data.items ?? data.results ?? [];
      total = data.total ?? Number(res.headers.get("X-Total-Count") ?? 0);
    }
    return json({ results, total });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de búsqueda." },
      { status: 503 }
    );
  }
}
export {
  GET
};
