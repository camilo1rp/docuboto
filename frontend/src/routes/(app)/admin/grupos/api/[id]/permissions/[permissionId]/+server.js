// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/grupos/api/[id]/permissions/[permissionId] — Proxy SSR: fija el
 * nivel CRUD de un permiso para un grupo (D-05, E08).
 *
 * PUT → body { crud: 0..5 }. USUA_PERM_ADMIN. Upsert en el backend
 * (ON CONFLICT DO UPDATE) — siempre 200, sin distinguir alta de actualización.
 *
 * GAP DE BACKEND: no hay `GET /groups/{id}/permissions` — esta acción es
 * "ciega": el front no puede mostrar el crud actual, solo fijar uno nuevo.
 * Ver aviso en +page.server.js y en la UI.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function PUT({ locals, params, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const { id, permissionId } = params;
  if (!id || !UUID_RE.test(id) || !permissionId || !UUID_RE.test(permissionId)) {
    return json({ error: "validation_error", detail: "Identificador inválido." }, { status: 400 });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  // Allowlist explícita — solo `crud` (anti mass-assignment).
  const { crud } = body ?? {};
  // `Number(null)` es 0 y `Number("")` también — se rechazan explícitamente
  // en vez de dejar que la coerción silenciosa las convierta en un crud=0
  // "válido" que el cliente nunca quiso enviar.
  if (crud === null || crud === undefined || crud === "") {
    return json(
      { error: "validation_error", detail: "El nivel CRUD es obligatorio." },
      { status: 400 },
    );
  }
  const crudNum = Number(crud);
  if (!Number.isInteger(crudNum) || crudNum < 0 || crudNum > 5) {
    return json(
      { error: "validation_error", detail: "El nivel CRUD debe ser un entero entre 0 y 5." },
      { status: 400 },
    );
  }

  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(
      `${API_V1}/auth/groups/${encodeURIComponent(id)}/permissions/${encodeURIComponent(permissionId)}`,
      { method: "PUT", headers, body: JSON.stringify({ crud: crudNum }) },
    );
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo actualizar el permiso del grupo." },
      { status: 503 },
    );
  }
}
