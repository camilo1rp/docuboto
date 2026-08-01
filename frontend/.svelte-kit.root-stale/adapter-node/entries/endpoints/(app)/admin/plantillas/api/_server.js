import { j as json } from "../../../../../../chunks/index.js";
import { A as API_V1 } from "../../../../../../chunks/config.server.js";
import { a as attachSafeCuerpo, s as sanitizeHtml } from "../../../../../../chunks/sanitizeHtml.js";
async function GET({ locals, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const activo = url.searchParams.get("activo") ?? "";
  const params = new URLSearchParams();
  if (activo) params.set("activo", activo);
  try {
    const res = await fetch(`${API_V1}/plantillas?${params}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant
      }
    });
    const data = await res.json().catch(() => []);
    if (!res.ok) return json(data, { status: res.status });
    if (Array.isArray(data)) data.forEach(attachSafeCuerpo);
    return json(data);
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de plantillas." },
      { status: 503 }
    );
  }
}
async function POST({ locals, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }
  const { nombre, tipo_documental, cuerpo: rawCuerpo, activo } = body ?? {};
  const cleanCuerpo = sanitizeHtml(typeof rawCuerpo === "string" ? rawCuerpo : "") || null;
  const payload = {
    nombre: typeof nombre === "string" ? nombre.trim() : "",
    tipo_documental: typeof tipo_documental === "string" && tipo_documental.trim() ? tipo_documental.trim() : null,
    cuerpo: cleanCuerpo,
    activo: typeof activo === "boolean" ? activo : true
  };
  try {
    const res = await fetch(`${API_V1}/plantillas`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant
      },
      body: JSON.stringify(payload)
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) attachSafeCuerpo(data);
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de plantillas." },
      { status: 503 }
    );
  }
}
export {
  GET,
  POST
};
