import { j as json } from "../../../../../chunks/index.js";
import { A as API_V1 } from "../../../../../chunks/config.server.js";
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const ESTADOS_VALIDOS = ["pendiente", "enviada", "recibida", "rechazada"];
const TIPOS_VALIDOS = ["primaria", "secundaria"];
async function GET({ locals, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const params = new URLSearchParams();
  const estado = url.searchParams.get("estado") ?? "";
  const tipo = url.searchParams.get("tipo") ?? "";
  if (estado && ESTADOS_VALIDOS.includes(estado)) params.set("estado", estado);
  if (tipo && TIPOS_VALIDOS.includes(tipo)) params.set("tipo", tipo);
  const qs = params.toString();
  try {
    const res = await fetch(`${API_V1}/transferencias${qs ? `?${qs}` : ""}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant
      }
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo obtener las transferencias." },
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
  const userId = locals.user?.sub ?? "";
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }
  const { tipo, expediente_id, ubicacion_origen_id, ubicacion_destino_id, observacion } = body ?? {};
  if (!tipo || !TIPOS_VALIDOS.includes(tipo)) {
    return json(
      {
        error: "validation_error",
        detail: `El campo 'tipo' es obligatorio. Valores: ${TIPOS_VALIDOS.join(", ")}.`
      },
      { status: 422 }
    );
  }
  if (!expediente_id || typeof expediente_id !== "string" || !UUID_RE.test(expediente_id.trim())) {
    return json(
      {
        error: "validation_error",
        detail: "El campo 'expediente_id' es obligatorio y debe ser un UUID válido."
      },
      { status: 422 }
    );
  }
  const payload = { tipo, expediente_id: expediente_id.trim() };
  if (ubicacion_origen_id && typeof ubicacion_origen_id === "string" && UUID_RE.test(ubicacion_origen_id.trim())) {
    payload.ubicacion_origen_id = ubicacion_origen_id.trim();
  }
  if (ubicacion_destino_id && typeof ubicacion_destino_id === "string" && UUID_RE.test(ubicacion_destino_id.trim())) {
    payload.ubicacion_destino_id = ubicacion_destino_id.trim();
  }
  if (observacion && typeof observacion === "string" && observacion.trim()) {
    payload.observacion = observacion.trim().slice(0, 2e3);
  }
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant
  };
  if (userId) headers["X-User-Id"] = userId;
  try {
    const res = await fetch(`${API_V1}/transferencias`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload)
    });
    const data = await res.json().catch(() => ({ ok: true }));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo crear la transferencia." },
      { status: 503 }
    );
  }
}
export {
  GET,
  POST
};
