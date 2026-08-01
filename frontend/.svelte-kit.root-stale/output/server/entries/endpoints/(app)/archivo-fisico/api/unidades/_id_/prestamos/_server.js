import { j as json } from "../../../../../../../../chunks/index.js";
import { A as API_V1 } from "../../../../../../../../chunks/config.server.js";
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
function isValidDate(s) {
  if (!DATE_RE.test(s)) return false;
  const ts = Date.parse(s);
  return !Number.isNaN(ts);
}
async function GET({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const { id } = params;
  try {
    const res = await fetch(`${API_V1}/unidades/${id}/prestamos`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant
      }
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo obtener los préstamos de la unidad." },
      { status: 503 }
    );
  }
}
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
  const { prestado_a, prestado_por, fecha_devolucion_esperada } = body ?? {};
  if (!prestado_a || typeof prestado_a !== "string" || !prestado_a.trim()) {
    return json(
      { error: "validation_error", detail: "El campo 'prestado_a' es obligatorio." },
      { status: 422 }
    );
  }
  if (!fecha_devolucion_esperada || typeof fecha_devolucion_esperada !== "string" || !isValidDate(fecha_devolucion_esperada.trim())) {
    return json(
      {
        error: "validation_error",
        detail: "El campo 'fecha_devolucion_esperada' es obligatorio y debe tener formato YYYY-MM-DD."
      },
      { status: 422 }
    );
  }
  const payload = {
    prestado_a: prestado_a.trim(),
    fecha_devolucion_esperada: fecha_devolucion_esperada.trim()
  };
  if (prestado_por && typeof prestado_por === "string" && prestado_por.trim()) {
    payload.prestado_por = prestado_por.trim();
  }
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant
  };
  if (userId) headers["X-User-Id"] = userId;
  try {
    const res = await fetch(`${API_V1}/unidades/${id}/prestamos`, {
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
      { error: "connection_error", detail: "No se pudo registrar el préstamo." },
      { status: 503 }
    );
  }
}
export {
  GET,
  POST
};
