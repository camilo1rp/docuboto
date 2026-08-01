// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/grupos/api/[id]/members/[userId] — Proxy SSR: alta/baja de
 * membresía de un usuario en un grupo (D-05, E08).
 *
 * POST   → agrega al usuario al grupo (201). USUA_PERM_ADMIN.
 * DELETE → quita al usuario del grupo (204, idempotente en el backend —
 *          no falla si el usuario no era miembro).
 *
 * GAP DE BACKEND: no hay `GET /groups/{id}/members` — esta acción es
 * "ciega": el front no puede confirmar contra el servidor quién es miembro
 * hoy, solo aplicar el cambio. Ver aviso en +page.server.js y en la UI.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function headersFor(locals) {
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  /** @type {Record<string, string>} */
  const headers = { Authorization: `Bearer ${locals.accessToken}`, "X-Tenant-Slug": tenant };
  if (userId) headers["X-User-Id"] = userId;
  return headers;
}

function validParams(params) {
  const { id, userId } = params;
  return id && UUID_RE.test(id) && userId && UUID_RE.test(userId);
}

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  if (!validParams(params)) {
    return json({ error: "validation_error", detail: "Identificador inválido." }, { status: 400 });
  }

  try {
    const res = await fetch(
      `${API_V1}/auth/groups/${encodeURIComponent(params.id)}/members/${encodeURIComponent(params.userId)}`,
      { method: "POST", headers: headersFor(locals) },
    );
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo agregar al miembro." },
      { status: 503 },
    );
  }
}

/** @type {import('./$types').RequestHandler} */
export async function DELETE({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  if (!validParams(params)) {
    return json({ error: "validation_error", detail: "Identificador inválido." }, { status: 400 });
  }

  try {
    const res = await fetch(
      `${API_V1}/auth/groups/${encodeURIComponent(params.id)}/members/${encodeURIComponent(params.userId)}`,
      { method: "DELETE", headers: headersFor(locals) },
    );
    if (res.status === 204) return new Response(null, { status: 204 });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo quitar al miembro." },
      { status: 503 },
    );
  }
}
