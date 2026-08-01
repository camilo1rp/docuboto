import { j as json } from "../../../../../../chunks/index.js";
import { A as API_V1 } from "../../../../../../chunks/config.server.js";
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const TIPOS_UNIDAD = ["caja", "carpeta", "legajo", "libro", "tomo", "az", "folder"];
async function GET({ locals, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const params = new URLSearchParams();
  const ubicacionId = url.searchParams.get("ubicacion_id") ?? "";
  const tipo = url.searchParams.get("tipo") ?? "";
  if (ubicacionId && UUID_RE.test(ubicacionId)) params.set("ubicacion_id", ubicacionId);
  if (tipo && TIPOS_UNIDAD.includes(tipo)) params.set("tipo", tipo);
  const qs = params.toString();
  try {
    const res = await fetch(`${API_V1}/unidades${qs ? `?${qs}` : ""}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant
      }
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servicio de archivo." },
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
  const { tipo, codigo, ubicacion_id, parent_id, soporte, num_folios } = body ?? {};
  if (!tipo || !TIPOS_UNIDAD.includes(tipo)) {
    return json(
      {
        error: "validation_error",
        detail: `El campo 'tipo' es obligatorio. Valores: ${TIPOS_UNIDAD.join(", ")}.`
      },
      { status: 422 }
    );
  }
  if (!codigo || typeof codigo !== "string" || !codigo.trim()) {
    return json(
      { error: "validation_error", detail: "El campo 'codigo' es obligatorio." },
      { status: 422 }
    );
  }
  const payload = { tipo, codigo: codigo.trim() };
  if (ubicacion_id && typeof ubicacion_id === "string" && UUID_RE.test(ubicacion_id.trim())) {
    payload.ubicacion_id = ubicacion_id.trim();
  }
  if (parent_id && typeof parent_id === "string" && UUID_RE.test(parent_id.trim())) {
    payload.parent_id = parent_id.trim();
  }
  if (soporte && typeof soporte === "string" && soporte.trim()) {
    payload.soporte = soporte.trim();
  }
  if (num_folios !== void 0 && num_folios !== null && Number.isFinite(Number(num_folios))) {
    payload.num_folios = Number(num_folios);
  }
  try {
    const res = await fetch(`${API_V1}/unidades`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant
      },
      body: JSON.stringify(payload)
    });
    const data = await res.json().catch(() => ({ ok: true }));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo crear la unidad." },
      { status: 503 }
    );
  }
}
export {
  GET,
  POST
};
