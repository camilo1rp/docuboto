// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/pinar/api/planes/[id]/tablero — Proxy SSR: tablero de avance del
 * plan PINAR (D-05, RF-ADM-08, Ac. AGN 003/2015, Fase 7).
 *
 * GET → TableroResponse { plan_id, por_objetivo[], por_eje[] }. Solo
 *   autenticado en el backend (consulta, services/tenant-service/app/
 *   routers/pinar.py:600-610).
 *
 * `admin/pinar/[id]/+page.server.js` ya carga el tablero en SSR junto con el
 * plan; este proxy existe para el botón "Actualizar" de la pestaña Tablero
 * (los proyectos/seguimiento que alimentan el avance pueden cambiar entre
 * cargas de página sin que el usuario recargue toda la vista).
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, params }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  const { id } = params;
  if (!id || !UUID_RE.test(id)) {
    return json(
      { error: "validation_error", detail: "Identificador de plan inválido." },
      { status: 400 },
    );
  }

  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  /** @type {Record<string, string>} */
  const headers = { Authorization: `Bearer ${locals.accessToken}`, "X-Tenant-Slug": tenant };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/pinar/planes/${encodeURIComponent(id)}/tablero`, {
      headers,
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo cargar el tablero del plan PINAR." },
      { status: 503 },
    );
  }
}
