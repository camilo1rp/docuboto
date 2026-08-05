// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/usuarios/api — Proxy SSR: listar y crear usuarios (D-05, E08).
 *
 * GET  ?page=&size=  → UserResponse[] (X-Total-Count reenviado). USUA_PERM_ADMIN.
 * POST               → crea un usuario. Body: { username, email?, keycloak_sub?,
 *                       is_root? }. USUA_PERM_ADMIN.
 *
 * NO existe en el backend (services/auth-service/app/routers/rbac.py):
 * actualizar, desactivar ni borrar un usuario — solo alta + lectura. La UI
 * NO ofrece esas acciones (no hay nada que "ocultar por RBAC": simplemente
 * no existen).
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  const pageParam = Number(url.searchParams.get("page") ?? "1");
  const page = Number.isFinite(pageParam) && pageParam >= 1 ? pageParam : 1;
  const sizeParam = Number(url.searchParams.get("size") ?? "20");
  const size = Number.isFinite(sizeParam) ? Math.min(Math.max(sizeParam, 1), 100) : 20;

  /** @type {Record<string, string>} */
  const headers = { Authorization: `Bearer ${token}`, "X-Tenant-Slug": tenant };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/auth/users?page=${page}&size=${size}`, { headers });
    const data = await res.json().catch(() => []);
    if (!res.ok) return json(data, { status: res.status });

    const total = res.headers.get("X-Total-Count") ?? String(Array.isArray(data) ? data.length : 0);
    return new Response(JSON.stringify(data), {
      status: 200,
      headers: { "Content-Type": "application/json", "X-Total-Count": total },
    });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de usuarios." },
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

  // Allowlist explícita — nunca reenviar id/is_active ni el body íntegro
  // (anti mass-assignment: is_root es un campo sensible, se acepta EXPLÍCITAMENTE
  // como booleano, no se infiere de nada más del payload del cliente).
  const { username, email, keycloak_sub, is_root } = body ?? {};

  if (typeof username !== "string" || !username.trim()) {
    return json(
      { error: "validation_error", detail: "El nombre de usuario es obligatorio." },
      { status: 400 },
    );
  }

  const payload = {
    username: username.trim(),
    email: typeof email === "string" && email.trim() ? email.trim() : null,
    keycloak_sub:
      typeof keycloak_sub === "string" && keycloak_sub.trim() ? keycloak_sub.trim() : null,
    is_root: is_root === true,
  };

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/auth/users`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo crear el usuario." },
      { status: 503 },
    );
  }
}
