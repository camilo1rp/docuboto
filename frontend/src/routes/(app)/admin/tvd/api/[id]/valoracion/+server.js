// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/tvd/api/[id]/valoracion — Proxy SSR: valoración del Comité sobre una
 * TVD (D-05, C7). Es el acto que levanta la retención legal `valoracion_pendiente`
 * de los expedientes de fondo acumulado.
 *
 * GET  → GET  /tvd/{id}/valoracion   (acto + {pendientes, aplicadas, agotadas})
 * POST → POST /tvd/{id}/valoracion   {acta_comite, fecha_acta, fundamento}
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";
import { construirValoracion } from "$lib/utils/valoracion.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @param {any} locals @param {boolean} [ct] */
function headers(locals, ct = false) {
  /** @type {Record<string,string>} */
  const h = {
    Authorization: `Bearer ${locals.accessToken}`,
    "X-Tenant-Slug": locals.user?.tenant_slug ?? "",
  };
  if (locals.user?.sub) h["X-User-Id"] = locals.user.sub;
  if (ct) h["Content-Type"] = "application/json";
  return h;
}

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, params }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  if (!UUID_RE.test(params.id ?? "")) {
    return json({ error: "validation_error", detail: "Identificador inválido." }, { status: 400 });
  }
  try {
    const res = await fetch(`${API_V1}/tvd/${encodeURIComponent(params.id)}/valoracion`, {
      headers: headers(locals),
    });
    return json(await res.json().catch(() => ({})), { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo consultar la valoración." },
      { status: 503 },
    );
  }
}

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, params, request }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  if (!UUID_RE.test(params.id ?? "")) {
    return json({ error: "validation_error", detail: "Identificador inválido." }, { status: 400 });
  }
  const body = await request.json().catch(() => null);
  const c = construirValoracion(body);
  if ("error" in c) return json({ error: "validation_error", detail: c.error }, { status: 422 });
  try {
    const res = await fetch(`${API_V1}/tvd/${encodeURIComponent(params.id)}/valoracion`, {
      method: "POST",
      headers: headers(locals, true),
      body: JSON.stringify(c.ok),
    });
    return json(await res.json().catch(() => ({})), { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo registrar la valoración." },
      { status: 503 },
    );
  }
}
