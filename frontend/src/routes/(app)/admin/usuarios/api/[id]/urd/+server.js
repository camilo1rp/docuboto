// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/usuarios/api/[id]/urd — Proxy SSR: URD (Usuario-Rol-Dependencia) de
 * un usuario (D-05, E08).
 *
 * GET  → URDResponse[] del usuario.
 * POST → crea una URD. Body: { depe_id: number (código de dependencia,
 *   1..999999), rol_id: string (uuid, en realidad un auth_groups.id — ver
 *   nota en +page.server.js), es_principal?: boolean }.
 *
 * USUA_PERM_ADMIN en el backend (services/auth-service/app/routers/urd.py).
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function headersFor(locals) {
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  /** @type {Record<string, string>} */
  const headers = { Authorization: `Bearer ${locals.accessToken}`, "X-Tenant-Slug": tenant };
  if (userId) headers["X-User-Id"] = userId;
  return headers;
}

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const { id } = params;
  if (!id || !UUID_RE.test(id)) {
    return json({ error: "validation_error", detail: "Identificador de usuario inválido." }, { status: 400 });
  }

  try {
    const res = await fetch(`${API_V1}/auth/users/${encodeURIComponent(id)}/urd`, {
      headers: headersFor(locals),
    });
    const data = await res.json().catch(() => []);
    return json(data, { status: res.status });
  } catch {
    return json({ error: "connection_error", detail: "No se pudo cargar la URD." }, { status: 503 });
  }
}

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, params, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const { id } = params;
  if (!id || !UUID_RE.test(id)) {
    return json({ error: "validation_error", detail: "Identificador de usuario inválido." }, { status: 400 });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  // Allowlist explícita — solo estos tres campos (anti mass-assignment).
  const { depe_id: depeId, rol_id: rolId, es_principal: esPrincipal } = body ?? {};

  const depeIdNum = Number(depeId);
  if (!Number.isInteger(depeIdNum) || depeIdNum < 1 || depeIdNum > 999999) {
    return json({ error: "validation_error", detail: "Código de dependencia inválido." }, { status: 400 });
  }
  if (typeof rolId !== "string" || !UUID_RE.test(rolId)) {
    return json({ error: "validation_error", detail: "Rol (grupo) inválido." }, { status: 400 });
  }

  const payload = { depe_id: depeIdNum, rol_id: rolId, es_principal: esPrincipal === true };

  try {
    const res = await fetch(`${API_V1}/auth/users/${encodeURIComponent(id)}/urd`, {
      method: "POST",
      headers: { ...headersFor(locals), "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json({ error: "connection_error", detail: "No se pudo asignar la URD." }, { status: 503 });
  }
}
