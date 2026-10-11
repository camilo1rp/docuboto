// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/flujos/reglas/api — Proxy SSR: listar y crear reglas de enrutamiento
 * automático (E05, `workflow-service/app/routers/rules.py`, D-05).
 *
 * GET  ?active_only=&page=&size=  → WorkflowRuleResponse[] (sin X-Total-Count:
 *      `list_rules` del backend no pagina con cabecera de total; la pantalla
 *      pide una página grande y pagina/filtra localmente).
 * POST → crea una regla. Body allowlist (nunca el body íntegro del cliente):
 *      { name, description?, is_active?, priority?, conditions?, match_mode?,
 *        assign_to_dept_code, assign_notes? }
 *      `conditions[]` se reconstruye campo a campo con `buildConditionsPayload`
 *      (allowlist de field/op del enum cerrado del backend + coerción de
 *      value) — nunca se reenvía la condición cruda del cliente.
 *
 * Ambos requieren USUA_PERM_ADMIN: el backend gatea `GET`/`POST
 * /workflow/rules` con `require_permission("USUA_PERM_ADMIN")` y responde
 * 403 si falta (ver nota en `+page.svelte`); este proxy no añade un guard de
 * permiso propio porque sería una segunda fuente de verdad — se limita a
 * propagar el status/detail que devuelva el gateway (D-05 regla 3).
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";
import { parseCodigoDependencia } from "$lib/utils/dependenciaNombre.js";
import { buildConditionsPayload } from "$lib/utils/workflowRuleConditions.js";

const MATCH_MODES = new Set(["all", "any"]);

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";

  const params = new URLSearchParams();
  const activeOnly = url.searchParams.get("active_only");
  if (activeOnly !== null) params.set("active_only", activeOnly);
  params.set("page", url.searchParams.get("page") ?? "1");
  params.set("size", url.searchParams.get("size") ?? "200");

  try {
    const res = await fetch(`${API_V1}/workflow/rules?${params}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
    });
    const data = await res.json().catch(() => []);
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de reglas." },
      { status: 503 },
    );
  }
}

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

  const {
    name,
    description,
    is_active,
    priority,
    conditions: rawConditions,
    match_mode,
    assign_to_dept_code,
    assign_notes,
  } = body ?? {};

  if (typeof name !== "string" || !name.trim()) {
    return json(
      { error: "validation_error", detail: "El nombre de la regla es obligatorio." },
      { status: 422 },
    );
  }
  const assignCode = parseCodigoDependencia(assign_to_dept_code);
  if (assignCode === null) {
    return json(
      {
        error: "validation_error",
        detail: "La dependencia de destino de la acción es obligatoria (código entero positivo).",
      },
      { status: 422 },
    );
  }

  // La prioridad NO se recorta en silencio a un default: si el cliente la
  // omite, se usa 100; si la envía pero está fuera de 1..999, se rechaza con
  // 422 explícito — recortarla sin avisar guardaría una regla con una
  // prioridad distinta a la que el administrador escribió.
  let safePriority = 100;
  if (priority !== undefined && priority !== null && priority !== "") {
    const priorityNum = Number(priority);
    if (!Number.isFinite(priorityNum) || priorityNum < 1 || priorityNum > 999) {
      return json(
        {
          error: "validation_error",
          detail: "La prioridad debe ser un número entre 1 y 999.",
        },
        { status: 422 },
      );
    }
    safePriority = priorityNum;
  }

  // Allowlist campo a campo — nunca se reenvía `body` íntegro (anti mass-assignment).
  const payload = {
    name: name.trim().slice(0, 200),
    description: typeof description === "string" && description.trim() ? description.trim() : null,
    is_active: typeof is_active === "boolean" ? is_active : true,
    priority: safePriority,
    conditions: buildConditionsPayload(rawConditions),
    match_mode: MATCH_MODES.has(match_mode) ? match_mode : "all",
    assign_to_dept_code: assignCode,
    assign_notes:
      typeof assign_notes === "string" && assign_notes.trim() ? assign_notes.trim() : null,
  };

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/workflow/rules`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo crear la regla." },
      { status: 503 },
    );
  }
}
