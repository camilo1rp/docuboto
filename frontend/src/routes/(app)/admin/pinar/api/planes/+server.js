// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/pinar/api/planes — Proxy SSR: listar y crear planes PINAR (D-05,
 * RF-ADM-08, Ac. AGN 003/2015, Fase 7).
 *
 * GET  ?estado= → PlanResponse[]. Solo autenticado en el backend (consulta).
 * POST           → crea una versión nueva del plan (siempre en 'borrador').
 *   USUA_PERM_ADMIN en el backend (services/tenant-service/app/routers/
 *   pinar.py:115-125). El acto es imputable: `create_plan` exige X-User-Id
 *   válido (`_require_actor`) — sin sesión con `sub` el backend responde 400
 *   "actor_requerido", fail-closed (nunca un asiento con actor NULL).
 *
 * Body permitido (allowlist, anti mass-assignment): vision, vigencia_desde,
 * vigencia_hasta, acto_administrativo. La UI de creación del MVP (Fase 7) no
 * pide `acto_administrativo` al crear — se declara al aprobar (PlanAprobar),
 * que es donde la spec lo exige — pero el campo viaja si algún consumidor
 * futuro lo envía, porque el schema PlanCreate lo admite como opcional.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const ESTADOS = new Set(["borrador", "aprobado", "en_ejecucion", "cerrado"]);
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

/** @param {import('@sveltejs/kit').RequestEvent['locals']} locals */
function headersFor(locals, withContentType = false) {
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  /** @type {Record<string, string>} */
  const headers = { Authorization: `Bearer ${locals.accessToken}`, "X-Tenant-Slug": tenant };
  if (userId) headers["X-User-Id"] = userId;
  if (withContentType) headers["Content-Type"] = "application/json";
  return headers;
}

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, url }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  const params = new URLSearchParams();
  const estado = url.searchParams.get("estado");
  if (estado && ESTADOS.has(estado)) params.set("estado", estado);

  try {
    const qs = params.toString();
    const res = await fetch(`${API_V1}/pinar/planes${qs ? `?${qs}` : ""}`, {
      headers: headersFor(locals),
    });
    const data = await res.json().catch(() => []);
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor del PINAR." },
      { status: 503 },
    );
  }
}

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, request }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  const { vision, vigencia_desde, vigencia_hasta, acto_administrativo } = body ?? {};

  if (vigencia_desde !== undefined && vigencia_desde !== null && !DATE_RE.test(vigencia_desde)) {
    return json({ error: "validation_error", detail: "Fecha de inicio de vigencia inválida." }, { status: 400 });
  }
  if (vigencia_hasta !== undefined && vigencia_hasta !== null && !DATE_RE.test(vigencia_hasta)) {
    return json({ error: "validation_error", detail: "Fecha de fin de vigencia inválida." }, { status: 400 });
  }
  if (
    acto_administrativo !== undefined &&
    acto_administrativo !== null &&
    (typeof acto_administrativo !== "string" || acto_administrativo.length > 200)
  ) {
    return json({ error: "validation_error", detail: "Acto administrativo inválido." }, { status: 400 });
  }

  const payload = {
    vision: typeof vision === "string" && vision.trim() ? vision.trim() : null,
    vigencia_desde: DATE_RE.test(vigencia_desde) ? vigencia_desde : null,
    vigencia_hasta: DATE_RE.test(vigencia_hasta) ? vigencia_hasta : null,
    acto_administrativo:
      typeof acto_administrativo === "string" && acto_administrativo.trim()
        ? acto_administrativo.trim()
        : null,
  };

  try {
    const res = await fetch(`${API_V1}/pinar/planes`, {
      method: "POST",
      headers: headersFor(locals, true),
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json({ error: "connection_error", detail: "No se pudo crear el plan PINAR." }, { status: 503 });
  }
}
