import { j as json } from "../../../../../../../../chunks/index.js";
import { A as API_V1 } from "../../../../../../../../chunks/config.server.js";
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
async function GET({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const { id } = params;
  try {
    const res = await fetch(`${API_V1}/unidades/${id}/expedientes`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant
      }
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo obtener los expedientes de la unidad." },
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
  const { id } = params;
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }
  const { expediente_id, folio_inicio, folio_fin } = body ?? {};
  if (!expediente_id || typeof expediente_id !== "string" || !UUID_RE.test(expediente_id.trim())) {
    return json(
      {
        error: "validation_error",
        detail: "El campo 'expediente_id' es obligatorio y debe ser un UUID válido."
      },
      { status: 422 }
    );
  }
  const payload = { expediente_id: expediente_id.trim() };
  if (folio_inicio !== void 0 && folio_inicio !== null && Number.isFinite(Number(folio_inicio))) {
    payload.folio_inicio = Number(folio_inicio);
  }
  if (folio_fin !== void 0 && folio_fin !== null && Number.isFinite(Number(folio_fin))) {
    payload.folio_fin = Number(folio_fin);
  }
  try {
    const res = await fetch(`${API_V1}/unidades/${id}/expedientes`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant
      },
      body: JSON.stringify(payload)
    });
    if (res.status === 204) {
      return json({ ok: true }, { status: 200 });
    }
    const data = await res.json().catch(() => ({ ok: true }));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo vincular el expediente a la unidad." },
      { status: 503 }
    );
  }
}
export {
  GET,
  POST
};
