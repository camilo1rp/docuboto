// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/tvd/api/[id]/valoracion/expedientes/[expedienteId]/seleccion — Proxy SSR (D-05, C-5).
 *
 * POST → POST /tvd/{id}/valoracion/expedientes/{expedienteId}/seleccion {accion, fundamento}
 * Acto de selección (disposición S) sobre UN expediente: `conservar` sigue el camino de CT;
 * `descartar` levanta la retención legal y lo deja elegible para el lote. Allowlist del cuerpo.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";
import { construirSeleccion } from "$lib/utils/valoracion.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, params, request }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  if (!UUID_RE.test(params.id ?? "") || !UUID_RE.test(params.expedienteId ?? "")) {
    return json({ error: "validation_error", detail: "Identificador inválido." }, { status: 400 });
  }
  const body = await request.json().catch(() => null);
  const c = construirSeleccion(body);
  if ("error" in c) return json({ error: "validation_error", detail: c.error }, { status: 422 });
  try {
    const res = await fetch(
      `${API_V1}/tvd/${encodeURIComponent(params.id)}/valoracion/expedientes/${encodeURIComponent(params.expedienteId)}/seleccion`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${locals.accessToken}`,
          "X-Tenant-Slug": locals.user?.tenant_slug ?? "",
          "Content-Type": "application/json",
          ...(locals.user?.sub ? { "X-User-Id": locals.user.sub } : {}),
        },
        body: JSON.stringify(c.ok),
      },
    );
    return json(await res.json().catch(() => ({})), { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo registrar la selección." },
      { status: 503 },
    );
  }
}
