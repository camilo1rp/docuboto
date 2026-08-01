import { C as COOKIE_ACCESS_TOKEN } from "../../../../chunks/auth.server.js";
import { A as API_V1 } from "../../../../chunks/config.server.js";
const OPEN_STATUSES = ["distributed", "in_progress"];
const PENDIENTES_SCAN_SIZE = 20;
const WIDGET_LIMIT = 5;
async function fetchDocuments(fetchFn, token, tenant, params) {
  const res = await fetchFn(`${API_V1}/documents?${params.toString()}`, {
    headers: {
      Authorization: `Bearer ${token}`,
      "X-Tenant-Slug": tenant
    }
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body.detail ?? `Error ${res.status}`);
  }
  const total = Number(res.headers.get("X-Total-Count") ?? 0);
  const items = await res.json();
  return { items: Array.isArray(items) ? items : [], total };
}
async function load({ cookies, parent, fetch }) {
  const { user } = await parent();
  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";
  const canSeeSummary = (user?.permissions ?? []).includes("SGD_PERM_ESTADISTICA");
  const openStatusFetches = OPEN_STATUSES.map((status) => {
    const params = new URLSearchParams({
      status,
      page: "1",
      size: String(PENDIENTES_SCAN_SIZE)
    });
    return fetchDocuments(fetch, token, tenant, params);
  });
  const recentParams = new URLSearchParams({ page: "1", size: String(WIDGET_LIMIT) });
  const recentFetch = fetchDocuments(fetch, token, tenant, recentParams);
  const summaryFetch = (async () => {
    if (!canSeeSummary) return null;
    const res = await fetch(`${API_V1}/reports/radicados`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant
      }
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail ?? `Error ${res.status}`);
    }
    return res.json();
  })();
  const [openResults, recentResult, summaryResult] = await Promise.all([
    Promise.allSettled(openStatusFetches),
    Promise.allSettled([recentFetch]),
    Promise.allSettled([summaryFetch])
  ]);
  const fulfilledOpen = openResults.filter(
    /** @returns {r is PromiseFulfilledResult<{items: any[], total: number}>} */
    (r) => r.status === "fulfilled"
  );
  const firstRejectedOpen = openResults.find((r) => r.status === "rejected");
  const openError = fulfilledOpen.length === 0 && firstRejectedOpen ? firstRejectedOpen.reason?.message ?? "No se pudo conectar al servidor de documentos." : null;
  const openDocuments = fulfilledOpen.flatMap((r) => r.value.items);
  const pendientesTotal = fulfilledOpen.reduce((acc, r) => acc + r.value.total, 0);
  const pendientesItems = [...openDocuments].sort((a, b) => (b.registered_at ?? "").localeCompare(a.registered_at ?? "")).slice(0, WIDGET_LIMIT);
  const now = Date.now();
  const alertasItems = openDocuments.filter((doc) => !!doc.due_date).map((doc) => {
    const dueMs = new Date(doc.due_date).getTime();
    const diasRestantes = Number.isFinite(dueMs) ? Math.ceil((dueMs - now) / (1e3 * 60 * 60 * 24)) : null;
    return { ...doc, diasRestantes };
  }).filter((doc) => doc.diasRestantes !== null).sort((a, b) => a.diasRestantes - b.diasRestantes).slice(0, WIDGET_LIMIT);
  const recentSettled = recentResult[0];
  const recientesItems = recentSettled.status === "fulfilled" ? recentSettled.value.items : [];
  const recientesError = recentSettled.status === "rejected" ? recentSettled.reason?.message ?? "No se pudo conectar al servidor de documentos." : null;
  const summarySettled = summaryResult[0];
  const summary = summarySettled.status === "fulfilled" ? summarySettled.value : null;
  const summaryError = summarySettled.status === "rejected" ? summarySettled.reason?.message ?? "No se pudo conectar al servidor de reportes." : null;
  return {
    pendientes: { items: pendientesItems, total: pendientesTotal, error: openError },
    alertas: { items: alertasItems, error: openError },
    recientes: { items: recientesItems, error: recientesError },
    summary,
    summaryError
  };
}
export {
  load
};
