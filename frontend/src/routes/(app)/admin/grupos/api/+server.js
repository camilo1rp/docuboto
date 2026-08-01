// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/grupos/api — Proxy SSR: listar y crear grupos (D-05, E08).
 *
 * GET  → GroupResponse[]. USUA_PERM_ADMIN.
 * POST → crea un grupo. Body: { name, description? }. USUA_PERM_ADMIN.
 *
 * NO existe en el backend (services/auth-service/app/routers/rbac.py):
 * actualizar ni borrar un grupo — solo alta + lectura. La UI NO ofrece esas
 * acciones (no hay nada que "ocultar por RBAC": simplemente no existen).
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

function headersFor(locals) {
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  /** @type {Record<string, string>} */
  const headers = { Authorization: `Bearer ${locals.accessToken}`, "X-Tenant-Slug": tenant };
  if (userId) headers["X-User-Id"] = userId;
  return headers;
}

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  try {
    const res = await fetch(`${API_V1}/auth/groups`, { headers: headersFor(locals) });
    const data = await res.json().catch(() => []);
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de grupos." },
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

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  // Allowlist explícita — nunca reenviar el body íntegro (anti mass-assignment).
  const { name, description } = body ?? {};

  if (typeof name !== "string" || !name.trim()) {
    return json(
      { error: "validation_error", detail: "El nombre del grupo es obligatorio." },
      { status: 400 },
    );
  }

  const payload = {
    name: name.trim(),
    description: typeof description === "string" && description.trim() ? description.trim() : null,
  };

  try {
    const res = await fetch(`${API_V1}/auth/groups`, {
      method: "POST",
      headers: { ...headersFor(locals), "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo crear el grupo." },
      { status: 503 },
    );
  }
}
