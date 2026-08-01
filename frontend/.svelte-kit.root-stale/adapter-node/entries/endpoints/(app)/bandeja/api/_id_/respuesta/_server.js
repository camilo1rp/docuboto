import { j as json } from "../../../../../../../chunks/index.js";
import { A as API_V1 } from "../../../../../../../chunks/config.server.js";
async function POST({ locals, params, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  const { id } = params;
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }
  const { subject, dest_dept, dest_dept_code, observations, response_days, metadata } = body ?? {};
  const payload = {};
  if (subject && typeof subject === "string" && subject.trim()) {
    payload.subject = subject.trim().slice(0, 255);
  }
  if (dest_dept && typeof dest_dept === "string" && dest_dept.trim()) {
    payload.dest_dept = dest_dept.trim();
  }
  if (dest_dept_code !== void 0 && dest_dept_code !== null && dest_dept_code !== "") {
    const code = Number(dest_dept_code);
    if (!isNaN(code) && code > 0) payload.dest_dept_code = code;
  }
  if (observations && typeof observations === "string" && observations.trim()) {
    payload.observations = observations.trim().slice(0, 2e3);
  }
  if (response_days !== void 0 && response_days !== null && response_days !== "") {
    const days = Number(response_days);
    if (!isNaN(days) && days > 0) payload.response_days = days;
  }
  if (metadata && typeof metadata === "object") {
    payload.metadata = metadata;
  }
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant
  };
  if (userId) headers["X-User-Id"] = userId;
  try {
    const res = await fetch(`${API_V1}/documents/${id}/respuesta`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload)
    });
    if (res.status === 204) {
      return json({ ok: true }, { status: 200 });
    }
    const data = await res.json().catch(() => ({ ok: true }));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo crear el radicado de respuesta." },
      { status: 503 }
    );
  }
}
export {
  POST
};
