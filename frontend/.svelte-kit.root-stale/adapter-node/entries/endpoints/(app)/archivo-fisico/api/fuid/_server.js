import { j as json } from "../../../../../../chunks/index.js";
import { A as API_V1 } from "../../../../../../chunks/config.server.js";
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
async function GET({ locals, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const params = new URLSearchParams();
  const ubicacionId = url.searchParams.get("ubicacion_id") ?? "";
  const trdSerieId = url.searchParams.get("trd_serie_id") ?? "";
  if (ubicacionId && UUID_RE.test(ubicacionId)) params.set("ubicacion_id", ubicacionId);
  if (trdSerieId && UUID_RE.test(trdSerieId)) params.set("trd_serie_id", trdSerieId);
  const qs = params.toString();
  try {
    const res = await fetch(`${API_V1}/fuid${qs ? `?${qs}` : ""}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant
      }
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo consultar el FUID." },
      { status: 503 }
    );
  }
}
export {
  GET
};
