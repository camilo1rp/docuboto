// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/pinar/api/planes/[id]/ejecutar — Proxy SSR: pasa un plan PINAR
 * aprobado a ejecución (D-05, RF-ADM-08, Ac. AGN 003/2015, Fase 7).
 *
 * POST → aprobado → en_ejecucion. USUA_PERM_ADMIN en el backend
 *   (services/tenant-service/app/routers/pinar.py:186-200). Sin body.
 *   Invariante 1: a lo sumo un plan en 'en_ejecucion' por tenant (índice
 *   único parcial en BD) — si ya existe uno, el backend responde 409
 *   'plan_en_ejecucion_existe'; se propaga tal cual, la UI no lo enmascara.
 *   Transición irreversible (no hay 'volver' en la máquina de estados).
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, params }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  const { id } = params;
  if (!id || !UUID_RE.test(id)) {
    return json({ error: "validation_error", detail: "Identificador de plan inválido." }, { status: 400 });
  }

  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  /** @type {Record<string, string>} */
  const headers = { Authorization: `Bearer ${locals.accessToken}`, "X-Tenant-Slug": tenant };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/pinar/planes/${encodeURIComponent(id)}/ejecutar`, {
      method: "POST",
      headers,
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo pasar el plan PINAR a ejecución." },
      { status: 503 },
    );
  }
}
