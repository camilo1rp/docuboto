import { j as json } from "../../../../../../chunks/index.js";
import { A as API_V1 } from "../../../../../../chunks/config.server.js";
async function GET({ locals, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const tipoDocumental = url.searchParams.get("tipo_documental") ?? "";
  const params = new URLSearchParams();
  if (tipoDocumental) params.set("tipo_documental", tipoDocumental);
  const queryString = params.toString() ? `?${params}` : "";
  try {
    const res = await fetch(`${API_V1}/metadata/templates${queryString}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant
      }
    });
    const data = await res.json().catch(() => []);
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo cargar las plantillas de metadatos." },
      { status: 503 }
    );
  }
}
export {
  GET
};
