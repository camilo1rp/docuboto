// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/pinar/api/ejes — Proxy SSR: catálogo de ejes estratégicos del PINAR
 * (D-05, RF-ADM-08, Ac. AGN 003/2015, Fase 7).
 *
 * GET → EjeResponse[] { code, nombre }. Solo autenticado en el backend
 *   (services/tenant-service/app/routers/pinar.py:107-109) — es consulta de
 *   catálogo, no gestión, así que no exige USUA_PERM_ADMIN.
 *
 * El MVP de esta fase no incluye la pantalla de priorización (aspecto × eje);
 * este proxy queda preparado para ella y, mientras tanto, disponible como
 * referencia de los 5 ejes del PINAR en pantallas futuras del plan.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  /** @type {Record<string, string>} */
  const headers = { Authorization: `Bearer ${locals.accessToken}`, "X-Tenant-Slug": tenant };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/pinar/ejes`, { headers });
    const data = await res.json().catch(() => []);
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al catálogo de ejes del PINAR." },
      { status: 503 },
    );
  }
}
