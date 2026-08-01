import { j as json } from "../../../../../chunks/index.js";
import { A as API_V1 } from "../../../../../chunks/config.server.js";
import { a as attachSafeCuerpo, s as sanitizeHtml } from "../../../../../chunks/sanitizeHtml.js";
async function GET({ locals, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const estado = url.searchParams.get("estado") ?? "";
  const params = new URLSearchParams();
  if (estado) params.set("estado", estado);
  try {
    const res = await fetch(`${API_V1}/borradores?${params}`, {
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
      { error: "connection_error", detail: "No se pudo conectar al servidor de borradores." },
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
  const { doc_type, subject, dest_dept, cuerpo: rawCuerpo, plantilla_id } = body ?? {};
  const cleanCuerpo = sanitizeHtml(typeof rawCuerpo === "string" ? rawCuerpo : "") || null;
  const payload = {
    doc_type: doc_type === "I" ? "I" : "S",
    subject: typeof subject === "string" ? subject.trim() : "",
    dest_dept: typeof dest_dept === "string" && dest_dept.trim() ? dest_dept.trim() : null,
    cuerpo: cleanCuerpo,
    plantilla_id: typeof plantilla_id === "string" && plantilla_id ? plantilla_id : null
  };
  try {
    const res = await fetch(`${API_V1}/borradores`, {
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
      { error: "connection_error", detail: "No se pudo conectar al servidor de borradores." },
      { status: 503 }
    );
  }
}
export {
  GET,
  POST
};
