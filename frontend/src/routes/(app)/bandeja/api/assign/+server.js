// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /bandeja/api/assign — Proxy SSR: asigna un radicado a una dependencia (D-05,
 * ADR-011, E05 §Fase 1, RF-FLU-03). El backend re-valida PERM_TRAMITAR.
 *
 * El token y el userId se leen de event.locals. El JS del cliente nunca los ve.
 *
 * POST → crea el primer paso de flujo (`POST /workflows/assign`)
 *   Body (FlowStepCreate, allowlist): {
 *     radicado_id (uuid, requerido), tracking_number (str ≤50, requerido),
 *     to_dept (str ≤100, requerido), assigned_to (uuid, opcional),
 *     notes (str, opcional), doc_type (E|S|I, opcional), due_at (ISO datetime, opcional)
 *   }
 *   `action` NO se toma del cliente: este proxy siempre lo fija en "assign" — el
 *   endpoint es semánticamente "asignar", no una acción elegible por el body
 *   (anti mass-assignment / anti-confusión con transfer).
 *   Respuesta: FlowStepResponse o { error, detail, status }
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const VALID_DOC_TYPES = new Set(["E", "S", "I"]);

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  const { radicado_id, tracking_number, to_dept, assigned_to, notes, doc_type, due_at } =
    body ?? {};

  if (!radicado_id || typeof radicado_id !== "string" || !UUID_RE.test(radicado_id)) {
    return json(
      { error: "validation_error", detail: "radicado_id debe ser un UUID válido." },
      { status: 422 },
    );
  }
  if (!tracking_number || typeof tracking_number !== "string" || !tracking_number.trim()) {
    return json(
      { error: "validation_error", detail: "tracking_number es obligatorio." },
      { status: 422 },
    );
  }
  if (tracking_number.trim().length > 50) {
    return json(
      { error: "validation_error", detail: "tracking_number no puede superar 50 caracteres." },
      { status: 422 },
    );
  }
  if (!to_dept || typeof to_dept !== "string" || !to_dept.trim()) {
    return json(
      { error: "validation_error", detail: "to_dept (dependencia destino) es obligatorio." },
      { status: 422 },
    );
  }
  if (to_dept.trim().length > 100) {
    return json(
      { error: "validation_error", detail: "to_dept no puede superar 100 caracteres." },
      { status: 422 },
    );
  }
  if (assigned_to !== undefined && assigned_to !== null && assigned_to !== "") {
    if (typeof assigned_to !== "string" || !UUID_RE.test(assigned_to)) {
      return json(
        { error: "validation_error", detail: "assigned_to debe ser un UUID válido." },
        { status: 422 },
      );
    }
  }
  if (
    doc_type !== undefined &&
    doc_type !== null &&
    doc_type !== "" &&
    !VALID_DOC_TYPES.has(doc_type)
  ) {
    return json(
      { error: "validation_error", detail: "doc_type debe ser uno de: E, S, I." },
      { status: 422 },
    );
  }
  if (due_at !== undefined && due_at !== null && due_at !== "") {
    if (typeof due_at !== "string" || Number.isNaN(Date.parse(due_at))) {
      return json(
        { error: "validation_error", detail: "due_at debe ser una fecha ISO 8601 válida." },
        { status: 422 },
      );
    }
  }

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  // Allowlist campo a campo — `action` se fija aquí, no se lee del cliente.
  /** @type {Record<string, unknown>} */
  const payload = {
    radicado_id,
    tracking_number: tracking_number.trim(),
    to_dept: to_dept.trim(),
    action: "assign",
  };
  if (assigned_to) payload.assigned_to = assigned_to;
  if (typeof notes === "string" && notes.trim()) payload.notes = notes.trim().slice(0, 2000);
  if (doc_type) payload.doc_type = doc_type;
  if (due_at) payload.due_at = due_at;

  try {
    const res = await fetch(`${API_V1}/workflows/assign`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });

    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo asignar el radicado." },
      { status: 503 },
    );
  }
}
