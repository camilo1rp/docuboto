import { j as json } from "../../../../../../chunks/index.js";
import { A as API_V1 } from "../../../../../../chunks/config.server.js";
async function POST({ locals, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  let formData;
  try {
    formData = await request.formData();
  } catch {
    return json({ error: "invalid_form", detail: "FormData inválido." }, { status: 400 });
  }
  const file = formData.get("file");
  if (!file) {
    return json({ error: "missing_file", detail: "Campo 'file' requerido." }, { status: 400 });
  }
  const upstreamForm = new FormData();
  upstreamForm.append("file", file);
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant
  };
  if (userId) headers["X-User-Id"] = userId;
  try {
    const res = await fetch(`${API_V1}/storage/upload`, {
      method: "POST",
      headers,
      body: upstreamForm
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de almacenamiento." },
      { status: 503 }
    );
  }
}
export {
  POST
};
