import { j as json } from "../../../../../../chunks/index.js";
import { A as API_V1 } from "../../../../../../chunks/config.server.js";
import { a as attachSafeCuerpo, s as sanitizeHtml } from "../../../../../../chunks/sanitizeHtml.js";
async function GET({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  try {
    const res = await fetch(`${API_V1}/borradores/${params.id}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant
      }
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
async function PATCH({ locals, params, request }) {
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
  const { subject, dest_dept, cuerpo: rawCuerpo } = body ?? {};
  const cleanCuerpo = sanitizeHtml(typeof rawCuerpo === "string" ? rawCuerpo : "") || null;
  const payload = {};
  if (typeof subject === "string") payload.subject = subject.trim();
  if (typeof dest_dept === "string") payload.dest_dept = dest_dept.trim() || null;
  payload.cuerpo = cleanCuerpo;
  try {
    const res = await fetch(`${API_V1}/borradores/${params.id}`, {
      method: "PATCH",
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
  PATCH
};
