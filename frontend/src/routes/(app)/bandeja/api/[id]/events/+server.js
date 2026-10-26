// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /bandeja/api/[id]/events — Proxy SSR: eventos append-only de flujo de un
 * radicado (D-05, ADR-011, E05 §Fase 1, RF-FLU-02, hoja de ruta operativa).
 *
 * DIFERENCIA con /history y /current (verificada contra
 * `workflow-service/app/routers/workflow.py::get_flow_events`): este endpoint
 * usa `_require_user`, NO `_optional_user` — X-User-Id es OBLIGATORIO para el
 * backend (400 `missing_user` si falta), a diferencia del resto de lecturas de
 * la hoja de ruta que degradan a clearance PUBLICA sin identidad. Se reenvía
 * igual (X-User-Id desde locals si existe) — si `locals.user.sub` faltara, el
 * 400 del backend se propaga tal cual, no se enmascara.
 *
 * El token se lee de la cookie httpOnly del servidor y nunca se expone al JS del
 * cliente.
 *
 * GET → eventos append-only (`GET /workflows/{radicado_id}/events`)
 *   Respuesta: FlowEventResponse[] o { error, detail, status }
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  const { id } = params;
  if (!id || !UUID_RE.test(id)) {
    return json(
      { error: "validation_error", detail: "Identificador de radicado inválido." },
      { status: 400 },
    );
  }

  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/workflows/${encodeURIComponent(id)}/events`, { headers });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudieron cargar los eventos de flujo." },
      { status: 503 },
    );
  }
}
