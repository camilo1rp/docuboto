// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/usuarios/api/[id]/urd/[urdId] — Proxy SSR: borra una URD (D-05, E08).
 *
 * DELETE → 204 si existía y se borró; 404 si no.
 * USUA_PERM_ADMIN en el backend.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function DELETE({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  const { id, urdId } = params;
  if (!id || !UUID_RE.test(id) || !urdId || !UUID_RE.test(urdId)) {
    return json({ error: "validation_error", detail: "Identificador inválido." }, { status: 400 });
  }

  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  /** @type {Record<string, string>} */
  const headers = { Authorization: `Bearer ${token}`, "X-Tenant-Slug": tenant };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(
      `${API_V1}/auth/users/${encodeURIComponent(id)}/urd/${encodeURIComponent(urdId)}`,
      { method: "DELETE", headers },
    );
    if (res.status === 204) return new Response(null, { status: 204 });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo eliminar la URD." },
      { status: 503 },
    );
  }
}
