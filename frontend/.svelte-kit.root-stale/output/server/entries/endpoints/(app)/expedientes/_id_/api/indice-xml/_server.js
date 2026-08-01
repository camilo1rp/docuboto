import { A as API_V1 } from "../../../../../../../chunks/config.server.js";
import { j as json } from "../../../../../../../chunks/index.js";
async function GET({ locals, params, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const { id } = params;
  const version = url.searchParams.get("version");
  const qs = new URLSearchParams();
  if (version && /^\d+$/.test(version)) qs.set("version", version);
  const versionParam = qs.toString() ? `?${qs}` : "";
  let res;
  try {
    res = await fetch(`${API_V1}/expedientes/${id}/indice.xml${versionParam}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant
      }
    });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo descargar el índice XML." },
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
  return new Response(blob, {
    status: 200,
    headers: {
      "Content-Type": "application/xml; charset=utf-8"
    }
  });
}
export {
  GET
};
