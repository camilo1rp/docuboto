// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/trd/api/tipos/[id] — Proxy SSR: actualizar/borrar un tipo documental
 * (D-05, E03/RF-MET-07). USUA_PERM_TRD en el backend (PATCH y DELETE con
 * min_crud=3 en el borrado).
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function headersFor(locals, withContentType = false) {
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  /** @type {Record<string, string>} */
  const headers = { Authorization: `Bearer ${locals.accessToken}`, "X-Tenant-Slug": tenant };
  if (userId) headers["X-User-Id"] = userId;
  if (withContentType) headers["Content-Type"] = "application/json";
  return headers;
}

/** @type {import('./$types').RequestHandler} */
export async function PATCH({ locals, params, request }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const { id } = params;
  if (!id || !UUID_RE.test(id)) {
    return json({ error: "validation_error", detail: "Identificador de tipo inválido." }, { status: 400 });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  const { nombre, trd_serie_id, obligatorio, metadata_template, activo } = body ?? {};
  if (trd_serie_id !== undefined && trd_serie_id !== null && trd_serie_id !== "" && !UUID_RE.test(trd_serie_id)) {
    return json({ error: "validation_error", detail: "Serie TRD inválida." }, { status: 400 });
  }

  /** @type {Record<string, unknown>} */
  const payload = {};
  if (typeof nombre === "string" && nombre.trim()) payload.nombre = nombre.trim();
  if (trd_serie_id !== undefined) payload.trd_serie_id = trd_serie_id && UUID_RE.test(trd_serie_id) ? trd_serie_id : null;
  if (typeof obligatorio === "boolean") payload.obligatorio = obligatorio;
  if (metadata_template !== undefined) {
    payload.metadata_template =
      typeof metadata_template === "string" && metadata_template.trim() ? metadata_template.trim() : null;
  }
  if (typeof activo === "boolean") payload.activo = activo;

  try {
    const res = await fetch(`${API_V1}/tipos-documentales/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: headersFor(locals, true),
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json({ error: "connection_error", detail: "No se pudo actualizar el tipo documental." }, { status: 503 });
  }
}

/** @type {import('./$types').RequestHandler} */
export async function DELETE({ locals, params }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const { id } = params;
  if (!id || !UUID_RE.test(id)) {
    return json({ error: "validation_error", detail: "Identificador de tipo inválido." }, { status: 400 });
  }

  try {
    const res = await fetch(`${API_V1}/tipos-documentales/${encodeURIComponent(id)}`, {
      method: "DELETE",
      headers: headersFor(locals),
    });
    if (res.status === 204) return new Response(null, { status: 204 });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json({ error: "connection_error", detail: "No se pudo eliminar el tipo documental." }, { status: 503 });
  }
}
