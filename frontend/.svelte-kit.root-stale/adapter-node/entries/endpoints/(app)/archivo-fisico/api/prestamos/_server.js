import { j as json } from "../../../../../../chunks/index.js";
import { A as API_V1 } from "../../../../../../chunks/config.server.js";
const ESTADOS_VALIDOS = ["prestado", "devuelto", "vencido"];
async function GET({ locals, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const params = new URLSearchParams();
  const estado = url.searchParams.get("estado") ?? "";
  if (estado && ESTADOS_VALIDOS.includes(estado)) params.set("estado", estado);
  const qs = params.toString();
  try {
    const res = await fetch(`${API_V1}/prestamos${qs ? `?${qs}` : ""}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant
      }
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo obtener los préstamos." },
      { status: 503 }
    );
  }
}
export {
  GET
};
