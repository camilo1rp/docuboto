// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /bandeja/api/[id]/current — Proxy SSR: paso de flujo activo de un radicado
 * (D-05, ADR-011, E05 §Fase 1).
 *
 * El token se lee de la cookie httpOnly del servidor y nunca se expone al JS del
 * cliente. X-User-Id es OPCIONAL (igual que /history): el backend resuelve el
 * clearance real del llamante (RF-SEG-08, no-read-up) y degrada a PUBLICA sin
 * identidad. Un radicado sin paso activo devuelve 404 `no_active_step` (mismo
 * cuerpo neutro que un radicado sobre el clearance del llamante, no-oráculo) —
 * se reenvía tal cual.
 *
 * GET → paso activo (`GET /workflows/{radicado_id}/current`)
 *   Respuesta: FlowStepResponse o { error: "no_active_step", detail, status: 404 }
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
    const res = await fetch(`${API_V1}/workflows/${encodeURIComponent(id)}/current`, { headers });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo cargar el paso activo." },
      { status: 503 },
    );
  }
}
