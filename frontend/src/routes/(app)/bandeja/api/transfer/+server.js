// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /bandeja/api/transfer — Proxy SSR: traslada un radicado a otra dependencia
 * (D-05, ADR-011, E05 §Fase 1, RF-FLU-03). El backend re-valida PERM_TRAMITAR.
 *
 * El token y el userId se leen de event.locals. El JS del cliente nunca los ve.
 *
 * POST → crea un nuevo paso de flujo por traslado (`POST /workflows/transfer`)
 *   Body (FlowStepCreate, allowlist): {
 *     radicado_id (uuid, requerido), tracking_number (str ≤50, requerido),
 *     to_dept_code (int, requerido; ADR-032). El origen NO se recibe: el
 *     servidor lo deriva del paso activo (409 `origen_desactualizado` si cambió),
 *     assigned_to (uuid, opcional), notes (str, opcional), doc_type (E|S|I, opcional),
 *     due_at (ISO datetime, opcional)
 *   }
 *   `action` NO se toma del cliente: este proxy siempre lo fija en "transfer"
 *   (anti mass-assignment / anti-confusión con assign).
 *   Respuesta: FlowStepResponse o { error, detail, status }
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";
import { parseCodigoDependencia } from "$lib/utils/dependenciaNombre.js";

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

  const { radicado_id, tracking_number, to_dept_code, assigned_to, notes, doc_type, due_at } =
    body ?? {};

  if (!radicado_id || typeof radicado_id !== "string" || !UUID_RE.test(radicado_id)) {
    return json(
      {
        error: "validation_error",
        detail: "El radicado indicado no es válido. Recargue la página e intente de nuevo.",
      },
      { status: 422 },
    );
  }
  if (!tracking_number || typeof tracking_number !== "string" || !tracking_number.trim()) {
    return json(
      {
        error: "validation_error",
        detail: "Falta el número de radicado. Recargue la página e intente de nuevo.",
      },
      { status: 422 },
    );
  }
  if (tracking_number.trim().length > 50) {
    return json(
      {
        error: "validation_error",
        detail: "El número de radicado supera los 50 caracteres permitidos.",
      },
      { status: 422 },
    );
  }
  const toDeptCode = parseCodigoDependencia(to_dept_code);
  if (toDeptCode === null) {
    return json(
      {
        error: "validation_error",
        detail: "Seleccione la dependencia destino del catálogo.",
      },
      { status: 422 },
    );
  }
  if (assigned_to !== undefined && assigned_to !== null && assigned_to !== "") {
    if (typeof assigned_to !== "string" || !UUID_RE.test(assigned_to)) {
      return json(
        { error: "validation_error", detail: "El funcionario seleccionado no es válido." },
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
      {
        error: "validation_error",
        detail: "El tipo de documento debe ser entrada, salida o interno.",
      },
      { status: 422 },
    );
  }
  if (due_at !== undefined && due_at !== null && due_at !== "") {
    if (typeof due_at !== "string" || Number.isNaN(Date.parse(due_at))) {
      return json(
        { error: "validation_error", detail: "La fecha de vencimiento no es válida." },
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
    to_dept_code: toDeptCode,
    action: "transfer",
  };
  if (assigned_to) payload.assigned_to = assigned_to;
  if (typeof notes === "string" && notes.trim()) payload.notes = notes.trim().slice(0, 2000);
  if (doc_type) payload.doc_type = doc_type;
  if (due_at) payload.due_at = due_at;

  try {
    const res = await fetch(`${API_V1}/workflows/transfer`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });

    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo trasladar el radicado." },
      { status: 503 },
    );
  }
}
