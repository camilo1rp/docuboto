// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/catalogos/api/[catalogo]/[id] — Proxy SSR: actualizar/borrar un
 * item de catálogo (D-05, E14).
 *
 * PATCH → allowlist: nombre, activo (CatalogoItemUpdate — `codigo` es
 *   inmutable, no forma parte del schema de actualización del backend).
 * DELETE.
 *
 * HALLAZGO DE DOMINIO (ver +page.server.js): sin `require_permission` en el
 * backend; gate USUA_PERM_ADMIN aplicado solo del lado del cliente.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const CATALOGO_RE = /^[a-z0-9-]{1,60}$/;
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

function validParams(params) {
  return CATALOGO_RE.test(params.catalogo) && UUID_RE.test(params.id);
}

/** @type {import('./$types').RequestHandler} */
export async function PATCH({ locals, params, request }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  if (!validParams(params)) {
    return json(
      { error: "validation_error", detail: "Catálogo o identificador inválido." },
      { status: 400 },
    );
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  // CatalogoItemUpdate (backend) solo admite nombre/activo — `codigo` es
  // inmutable tras crear el item, no viaja aunque el cliente lo envíe.
  const { nombre, activo } = body ?? {};
  /** @type {Record<string, unknown>} */
  const payload = {};
  if (typeof nombre === "string" && nombre.trim()) payload.nombre = nombre.trim();
  if (typeof activo === "boolean") payload.activo = activo;

  try {
    const res = await fetch(
      `${API_V1}/catalogos/${encodeURIComponent(params.catalogo)}/${encodeURIComponent(params.id)}`,
      {
        method: "PATCH",
        headers: headersFor(locals, true),
        body: JSON.stringify(payload),
      },
    );
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo actualizar el item del catálogo." },
      { status: 503 },
    );
  }
}

/** @type {import('./$types').RequestHandler} */
export async function DELETE({ locals, params }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  if (!validParams(params)) {
    return json(
      { error: "validation_error", detail: "Catálogo o identificador inválido." },
      { status: 400 },
    );
  }

  try {
    const res = await fetch(
      `${API_V1}/catalogos/${encodeURIComponent(params.catalogo)}/${encodeURIComponent(params.id)}`,
      {
        method: "DELETE",
        headers: headersFor(locals),
      },
    );
    if (res.status === 204) return new Response(null, { status: 204 });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo eliminar el item del catálogo." },
      { status: 503 },
    );
  }
}
