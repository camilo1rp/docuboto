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
  const revisores = Array.isArray(body?.revisores) ? body.revisores : null;
  if (!revisores || revisores.length === 0) {
    return json(
      { error: "validation_error", detail: "Debe seleccionar al menos un revisor." },
      { status: 422 }
    );
  }
  if (revisores.length > 20) {
    return json(
      { error: "validation_error", detail: "Máximo 20 revisores en la cadena." },
      { status: 422 }
    );
  }
  const allStrings = revisores.every((r) => typeof r === "string" && r.trim());
  if (!allStrings) {
    return json(
      { error: "validation_error", detail: "Identificadores de revisor inválidos." },
      { status: 422 }
    );
  }
  if (new Set(revisores).size !== revisores.length) {
    return json(
      { error: "validation_error", detail: "Hay revisores duplicados en la cadena." },
      { status: 422 }
    );
  }
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant
  };
  if (userId) headers["X-User-Id"] = userId;
  try {
    const res = await fetch(`${API_V1}/workflows/${id}/vistos-buenos`, {
      method: "POST",
      headers,
      body: JSON.stringify({ revisores })
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo crear la cadena de visto bueno." },
      { status: 503 }
    );
  }
}
export {
  POST
};
