// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * PUT /admin/inteligencia/api/niveles — Proxy de PUT /config/intelligence/levels.
 *
 * Solo RESERVADA (2) y CLASIFICADA (3). PÚBLICA se habilita al pasar a ppi.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

/**
 * @param {App.Locals} locals
 */
function headersDe(locals) {
  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${locals.accessToken}`,
    "X-Tenant-Slug": locals.user?.tenant_slug ?? "",
    "Content-Type": "application/json",
  };
  if (locals.user?.sub) headers["X-User-Id"] = locals.user.sub;
  return headers;
}

/** @type {import('./$types').RequestHandler} */
export async function PUT({ locals, request }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }
  if (body?.level !== 2 && body?.level !== 3) {
    return json(
      { error: "validation_error", detail: "El nivel es RESERVADA o CLASIFICADA." },
      { status: 422 },
    );
  }
  if (typeof body?.enabled !== "boolean") {
    return json(
      { error: "validation_error", detail: "Indique si el nivel queda habilitado." },
      { status: 400 },
    );
  }
  /** @type {{level: 2|3, enabled: boolean, statement?: string}} */
  const payload = { level: body.level, enabled: body.enabled };
  if (typeof body.statement === "string" && body.statement.trim()) {
    payload.statement = body.statement.trim().slice(0, 4000);
  }

  try {
    const res = await fetch(`${API_V1}/config/intelligence/levels`, {
      method: "PUT",
      headers: headersDe(locals),
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo actualizar el nivel." },
      { status: 503 },
    );
  }
}
