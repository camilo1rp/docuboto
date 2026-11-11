// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /radicar/api/ingesta/[ref] — estado (GET) y borrado (DELETE) de la preparación.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const REF_RE = /^[A-Za-z0-9._:~-]{1,256}$/;

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, params }) {
  return _call(locals, params, "GET");
}

/** @type {import('./$types').RequestHandler} */
export async function DELETE({ locals, params }) {
  return _call(locals, params, "DELETE");
}

/**
 * @param {{ accessToken?: string, user?: { tenant_slug?: string, sub?: string } }} locals
 * @param {{ ref?: string }} params
 * @param {"GET" | "DELETE"} method
 */
async function _call(locals, params, method) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const ref = typeof params.ref === "string" ? params.ref.trim() : "";
  if (!REF_RE.test(ref)) {
    return json(
      { error: "validation_error", detail: "La referencia no tiene la forma acordada." },
      { status: 422 },
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
    const res = await fetch(`${API_V1}/knowledge/ingesta/${encodeURIComponent(ref)}`, {
      method,
      headers,
    });
    if (res.status === 204) {
      return new Response(null, { status: 204 });
    }
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar con el servicio de conocimiento." },
      { status: 503 },
    );
  }
}
