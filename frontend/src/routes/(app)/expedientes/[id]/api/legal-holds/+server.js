// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /expedientes/[id]/api/legal-holds — Proxy SSR de retenciones legales (D-05, C7).
 *
 * GET  → GET  /expedientes/{id}/legal-holds
 * POST → POST /expedientes/{id}/legal-holds  {motivo_tipo, fundamento, acta_ref}
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";
import { construirFijar } from "$lib/utils/legalHold.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @param {any} locals */
function cabeceras(locals) {
  /** @type {Record<string,string>} */
  const h = {
    Authorization: `Bearer ${locals.accessToken}`,
    "X-Tenant-Slug": locals.user?.tenant_slug ?? "",
  };
  const uid = locals.user?.sub ?? "";
  if (uid) h["X-User-Id"] = uid;
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
    const res = await fetch(`${API_V1}/expedientes/${encodeURIComponent(params.id)}/legal-holds`, {
      headers: cabeceras(locals),
    });
    return json(await res.json().catch(() => ({})), { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudieron consultar las retenciones legales." },
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
  const c = construirFijar(body);
  if ("error" in c) return json({ error: "validation_error", detail: c.error }, { status: 422 });
  try {
    const res = await fetch(`${API_V1}/expedientes/${encodeURIComponent(params.id)}/legal-holds`, {
      method: "POST",
      headers: { ...cabeceras(locals), "Content-Type": "application/json" },
      body: JSON.stringify(c.ok),
    });
    return json(await res.json().catch(() => ({})), { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo fijar la retención legal." },
      { status: 503 },
    );
  }
}
