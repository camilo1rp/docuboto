// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/grupos/api/[id]/clearance — Proxy SSR: fija la clearance (nivel de
 * seguridad máximo) de un grupo (D-05, E08, RF-SEG-08).
 *
 * PUT → body { max_level: 1..3 }. USUA_PERM_ADMIN. Devuelve 204.
 *
 * GAP DE BACKEND: no hay `GET /groups/{id}/clearance` — esta acción es
 * "ciega": el front no puede mostrar el nivel actual, solo fijar uno nuevo.
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
  const { id } = params;
  if (!id || !UUID_RE.test(id)) {
    return json({ error: "validation_error", detail: "Identificador de grupo inválido." }, { status: 400 });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  // Allowlist explícita — solo `max_level` (anti mass-assignment).
  const { max_level: maxLevel } = body ?? {};
  const maxLevelNum = Number(maxLevel);
  if (!Number.isInteger(maxLevelNum) || maxLevelNum < 1 || maxLevelNum > 3) {
    return json(
      { error: "validation_error", detail: "El nivel máximo debe ser un entero entre 1 y 3." },
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
    const res = await fetch(`${API_V1}/auth/groups/${encodeURIComponent(id)}/clearance`, {
      method: "PUT",
      headers,
      body: JSON.stringify({ max_level: maxLevelNum }),
    });
    if (res.status === 204) return new Response(null, { status: 204 });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo actualizar la clearance del grupo." },
      { status: 503 },
    );
  }
}
