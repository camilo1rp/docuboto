import { j as json } from "../../../../../../chunks/index.js";
import { A as API_V1 } from "../../../../../../chunks/config.server.js";
async function GET({ locals, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  const dateFrom = url.searchParams.get("date_from") ?? "";
  const dateTo = url.searchParams.get("date_to") ?? "";
  const params = new URLSearchParams();
  if (dateFrom && /^\d{4}-\d{2}-\d{2}$/.test(dateFrom)) params.set("date_from", dateFrom);
  if (dateTo && /^\d{4}-\d{2}-\d{2}$/.test(dateTo)) params.set("date_to", dateTo);
  const qs = params.toString();
  let res;
  try {
    res = await fetch(`${API_V1}/reports/radicados.csv${qs ? `?${qs}` : ""}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
        "X-User-Id": userId
      }
    });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo descargar el reporte CSV." },
      { status: 503 }
    );
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({
      error: "upstream_error",
      detail: `HTTP ${res.status}`
    }));
    return json(body, { status: res.status });
  }
  const blob = await res.blob();
  const contentDisposition = res.headers.get("Content-Disposition") ?? "attachment; filename=radicados.csv";
  return new Response(blob, {
    status: 200,
    headers: {
      "Content-Type": "text/csv; charset=utf-8",
      "Content-Disposition": contentDisposition
    }
  });
}
export {
  GET
};
