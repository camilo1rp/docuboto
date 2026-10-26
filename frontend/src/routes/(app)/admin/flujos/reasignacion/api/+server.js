// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/flujos/reasignacion/api — Proxy SSR: ejecuta la reasignación en
 * cascada (`POST /workflows/reassign/cascade`, RF-FLU-08, USUA_PERM_ADMIN).
 *
 * Acción MASIVA E IRREVERSIBLE: mueve TODOS los pasos activos
 * (`status IN ('pending','in_progress')`) de un usuario o dependencia a un
 * nuevo responsable, sin volver a preguntar radicado por radicado. Por eso,
 * además del gate de permiso del backend, este proxy exige una confirmación
 * ESCRITA (`confirm: "REASIGNAR"`) como segunda barrera — capa de defensa en
 * profundidad; el backend no la conoce ni la exige, la exige este proxy.
 *
 * POST body: { from_assigned_to?: uuid, from_dept_code?: number, to_assigned_to: uuid,
 *              confirm: string }
 *   Allowlist campo a campo (nunca el body íntegro del cliente).
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";
import { parseCodigoDependencia } from "$lib/utils/dependenciaNombre.js";
import { REASSIGN_CONFIRM_PHRASE, isReassignConfirmed } from "$lib/utils/reassignConfirm.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  const { from_assigned_to, from_dept_code, to_assigned_to, confirm } = body ?? {};

  if (!isReassignConfirmed(confirm)) {
    return json(
      {
        error: "confirmation_required",
        detail: `Debe escribir "${REASSIGN_CONFIRM_PHRASE}" para confirmar la reasignación masiva.`,
      },
      { status: 422 },
    );
  }

  const hasFromUser = typeof from_assigned_to === "string" && from_assigned_to.trim();
  const fromDeptCode = parseCodigoDependencia(from_dept_code);
  const hasFromDept = fromDeptCode !== null;
  if (!hasFromUser && !hasFromDept) {
    return json(
      { error: "validation_error", detail: "Indique el usuario o la dependencia de origen." },
      { status: 422 },
    );
  }
  if (hasFromUser && !UUID_RE.test(from_assigned_to.trim())) {
    return json(
      { error: "validation_error", detail: "Identificador de usuario de origen inválido." },
      { status: 422 },
    );
  }
  if (typeof to_assigned_to !== "string" || !UUID_RE.test(to_assigned_to.trim())) {
    return json(
      { error: "validation_error", detail: "Debe seleccionar el usuario destino." },
      { status: 422 },
    );
  }

  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  if (!userId) {
    // El backend (`_require_user`) exige X-User-Id con 400 igualmente; se
    // adelanta aquí para no gastar la llamada de red.
    return json(
      { error: "missing_user", detail: "No se pudo identificar al usuario actual." },
      { status: 400 },
    );
  }

  // Allowlist campo a campo — nunca se reenvía `body` íntegro (anti mass-assignment).
  const payload = {
    to_assigned_to: to_assigned_to.trim(),
  };
  if (hasFromUser) payload.from_assigned_to = from_assigned_to.trim();
  if (hasFromDept) payload.from_dept_code = fromDeptCode;

  try {
    const res = await fetch(`${API_V1}/workflows/reassign/cascade`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
        "X-User-Id": userId,
      },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo ejecutar la reasignación." },
      { status: 503 },
    );
  }
}
