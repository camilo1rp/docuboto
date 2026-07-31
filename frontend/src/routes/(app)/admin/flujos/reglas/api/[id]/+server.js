// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/flujos/reglas/api/[id] — Proxy SSR: detalle, edición y borrado de
 * una regla de enrutamiento (E05, D-05).
 *
 * GET    → WorkflowRuleResponse o 404.
 * PATCH  → edición parcial. Body allowlist idéntica a la de creación, pero
 *          solo se reenvían las claves PRESENTES en el body del cliente (edición
 *          parcial real — enviar `undefined` para un campo no tocado haría que
 *          el backend lo borrase, ya que `WorkflowRuleUpdate` trata cada campo
 *          como "reemplazar si viene").
 * DELETE → 204 o 404.
 *
 * El id de ruta SIEMPRE se envuelve con `encodeURIComponent` antes de tocar
 * el gateway.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";
import { buildConditionsPayload } from "$lib/utils/workflowRuleConditions.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MATCH_MODES = new Set(["all", "any"]);

function invalidId() {
  return json(
    { error: "validation_error", detail: "Identificador de regla inválido." },
    { status: 400 },
  );
}

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const { id } = params;
  if (!id || !UUID_RE.test(id)) return invalidId();

  const tenant = locals.user?.tenant_slug ?? "";

  try {
    const res = await fetch(`${API_V1}/workflow/rules/${encodeURIComponent(id)}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo obtener la regla." },
      { status: 503 },
    );
  }
}

/** @type {import('./$types').RequestHandler} */
export async function PATCH({ locals, params, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const { id } = params;
  if (!id || !UUID_RE.test(id)) return invalidId();

  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  /** @type {any} */
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }
  body = body ?? {};

  if ("name" in body && (typeof body.name !== "string" || !body.name.trim())) {
    return json(
      { error: "validation_error", detail: "El nombre de la regla no puede quedar vacío." },
      { status: 422 },
    );
  }
  if (
    "assign_to_dept" in body &&
    (typeof body.assign_to_dept !== "string" || !body.assign_to_dept.trim())
  ) {
    return json(
      {
        error: "validation_error",
        detail: "La dependencia de destino de la acción no puede quedar vacía.",
      },
      { status: 422 },
    );
  }

  // Allowlist campo a campo — solo se reenvían las claves presentes en el
  // body del cliente (edición parcial real).
  const payload = {};
  if ("name" in body) payload.name = String(body.name).trim().slice(0, 200);
  if ("description" in body) {
    payload.description =
      typeof body.description === "string" && body.description.trim()
        ? body.description.trim()
        : null;
  }
  if ("is_active" in body) payload.is_active = Boolean(body.is_active);
  if ("priority" in body) {
    // Igual que en la creación: una prioridad fuera de 1..999 se rechaza con
    // 422 explícito, nunca se recorta en silencio a un valor distinto del
    // que el administrador escribió.
    const n = Number(body.priority);
    if (!Number.isFinite(n) || n < 1 || n > 999) {
      return json(
        {
          error: "validation_error",
          detail: "La prioridad debe ser un número entre 1 y 999.",
        },
        { status: 422 },
      );
    }
    payload.priority = n;
  }
  if ("conditions" in body) payload.conditions = buildConditionsPayload(body.conditions);
  if ("match_mode" in body) {
    payload.match_mode = MATCH_MODES.has(body.match_mode) ? body.match_mode : "all";
  }
  if ("assign_to_dept" in body) {
    payload.assign_to_dept = String(body.assign_to_dept).trim().slice(0, 200);
  }
  if ("assign_notes" in body) {
    payload.assign_notes =
      typeof body.assign_notes === "string" && body.assign_notes.trim()
        ? body.assign_notes.trim()
        : null;
  }

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/workflow/rules/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers,
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo actualizar la regla." },
      { status: 503 },
    );
  }
}

/** @type {import('./$types').RequestHandler} */
export async function DELETE({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const { id } = params;
  if (!id || !UUID_RE.test(id)) return invalidId();

  const tenant = locals.user?.tenant_slug ?? "";

  try {
    const res = await fetch(`${API_V1}/workflow/rules/${encodeURIComponent(id)}`, {
      method: "DELETE",
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
    });
    if (res.status === 204) return new Response(null, { status: 204 });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo eliminar la regla." },
      { status: 503 },
    );
  }
}
