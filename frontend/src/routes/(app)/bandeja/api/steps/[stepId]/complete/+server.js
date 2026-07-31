// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /bandeja/api/steps/[stepId]/complete — Proxy SSR: completa un paso de flujo
 * (D-05, ADR-011, E05 §Fase 1, PERM_TRAMITAR).
 *
 * ADVERTENCIA DE DISEÑO (no tocar sin releer): el backend cuelga esta ruta de
 * `PATCH /workflows/{step_id}/complete` — la entidad es un PASO (`flow_steps.id`),
 * NO el `radicado_id`. Por eso este proxy vive bajo `api/steps/[stepId]/` y no
 * bajo `api/[id]/`, que en el resto de esta carpeta siempre significa
 * radicado_id. Confundir ambos ids envía el PATCH contra un radicado inexistente
 * como "paso" (404 del backend) o, peor, completa el paso equivocado si por
 * casualidad ambos UUID coinciden con un registro real. El nombre del parámetro
 * de ruta (`stepId`, no `id`) es la barrera principal contra ese bug.
 *
 * El token y el userId se leen de event.locals. El JS del cliente nunca los ve.
 *
 * PATCH → marca el paso como completado (`PATCH /workflows/{step_id}/complete`)
 *   Body (FlowStepComplete, allowlist): { notes?: string }
 *   Respuesta: FlowStepResponse o { error, detail, status }
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function PATCH({ locals, params, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  const { stepId } = params;
  if (!stepId || !UUID_RE.test(stepId)) {
    return json(
      { error: "validation_error", detail: "Identificador de paso (step_id) inválido." },
      { status: 400 },
    );
  }

  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  const { notes } = body ?? {};
  if (notes !== undefined && notes !== null && typeof notes !== "string") {
    return json({ error: "validation_error", detail: "notes debe ser texto." }, { status: 422 });
  }

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  // Allowlist: solo `notes`, derivado del step_id de la RUTA (nunca de un
  // radicado_id colado en el body).
  /** @type {Record<string, unknown>} */
  const payload = {};
  if (typeof notes === "string" && notes.trim()) payload.notes = notes.trim().slice(0, 2000);

  try {
    const res = await fetch(`${API_V1}/workflows/${encodeURIComponent(stepId)}/complete`, {
      method: "PATCH",
      headers,
      body: JSON.stringify(payload),
    });

    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo completar el paso." },
      { status: 503 },
    );
  }
}
