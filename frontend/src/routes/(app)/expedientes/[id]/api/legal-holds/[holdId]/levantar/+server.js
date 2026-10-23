// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /expedientes/[id]/api/legal-holds/[holdId]/levantar — Proxy SSR (D-05, C7).
 *
 * POST → POST /expedientes/{id}/legal-holds/{holdId}/levantar
 *        {motivo_tipo (confirmación), fundamento, acta_ref}
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";
import { construirLevantar } from "$lib/utils/legalHold.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, params, request }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  if (!UUID_RE.test(params.id ?? "") || !UUID_RE.test(params.holdId ?? "")) {
    return json({ error: "validation_error", detail: "Identificador inválido." }, { status: 400 });
  }
  const body = await request.json().catch(() => null);
  const c = construirLevantar({
    motivo_confirmado: body?.motivo_tipo,
    fundamento: body?.fundamento,
    acta_ref: body?.acta_ref,
  });
  if ("error" in c) return json({ error: "validation_error", detail: c.error }, { status: 422 });
  /** @type {Record<string,string>} */
  const headers = {
    Authorization: `Bearer ${locals.accessToken}`,
    "X-Tenant-Slug": locals.user?.tenant_slug ?? "",
    "Content-Type": "application/json",
  };
  if (locals.user?.sub) headers["X-User-Id"] = locals.user.sub;
  try {
    const res = await fetch(
      `${API_V1}/expedientes/${encodeURIComponent(params.id)}/legal-holds/${encodeURIComponent(params.holdId)}/levantar`,
      { method: "POST", headers, body: JSON.stringify(c.ok) },
    );
    return json(await res.json().catch(() => ({})), { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo levantar la retención legal." },
      { status: 503 },
    );
  }
}
