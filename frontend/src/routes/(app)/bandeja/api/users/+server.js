// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /bandeja/api/users — Proxy SSR: lista de usuarios del tenant (D-05, ADR-011, T-08c).
 *
 * Alimenta el UserPicker para construir cadenas de visto bueno. El token, el
 * tenant y el actor se leen de event.locals (cookie httpOnly + claims del JWT);
 * el JS del cliente NUNCA ve el token. El gateway re-deriva X-User-Id/X-Tenant-Slug
 * del JWT firmado.
 *
 * Autorización (D-10): consume `GET /auth/users/pickable`, gateado con PERM_RADI en
 * auth-service (NO el `/auth/users` admin-only, que exige USUA_PERM_ADMIN). Devuelve
 * solo id+username (proyección mínima, sin email ni PII administrativa). El backend
 * es la compuerta autoritativa; la UI solo lo invoca tras $permissions=PERM_RADI.
 *
 * GET → list[UserPickItem] del auth-service.
 *   Query: ?size=N (default 100, máx 500).
 *   Respuesta: { users: [{ id, username }] } o { error, detail }.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";
import { codigoError, mensajeError } from "$lib/utils/apiError.js";

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  // size acotado a [1, 500] (límite del backend list_pickable_users).
  const sizeParam = Number(url.searchParams.get("size") ?? "100");
  const size = Number.isFinite(sizeParam) ? Math.min(Math.max(sizeParam, 1), 500) : 100;

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/auth/users/pickable?size=${size}`, { headers });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      return json(
        {
          error: codigoError(err) ?? "upstream_error",
          detail: mensajeError(err, "No se pudo cargar la lista de usuarios."),
        },
        { status: res.status },
      );
    }
    const list = await res.json().catch(() => []);
    const users = (Array.isArray(list) ? list : []).map((u) => ({
      id: u.id,
      username: u.username,
    }));
    return json({ users }, { status: 200 });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo cargar la lista de usuarios." },
      { status: 503 },
    );
  }
}
