import { j as json } from "../../../../../../chunks/index.js";
import { A as API_V1 } from "../../../../../../chunks/config.server.js";
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
async function GET({ locals, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const trdSerieId = url.searchParams.get("trd_serie_id") ?? "";
  if (!UUID_RE.test(trdSerieId)) {
    return json(
      { error: "invalid_param", detail: "trd_serie_id debe ser un UUID válido." },
      { status: 400 }
    );
  }
  try {
    const res = await fetch(
      `${API_V1}/expediente-metadata/templates?trd_serie_id=${encodeURIComponent(trdSerieId)}`,
      {
        headers: {
          Authorization: `Bearer ${token}`,
          "X-Tenant-Slug": tenant
        }
      }
    );
    const data = await res.json().catch(() => []);
    return json(data, { status: res.status });
  } catch {
    return json(
      {
        error: "connection_error",
        detail: "No se pudo cargar la plantilla de metadatos de la serie."
      },
      { status: 503 }
    );
  }
}
export {
  GET
};
