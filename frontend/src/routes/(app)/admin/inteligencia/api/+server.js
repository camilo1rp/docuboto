// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * PUT /admin/inteligencia/api — Proxy de PUT /config/intelligence (D-05).
 *
 * Allowlist: `provider`, `send_metadata` y `transmission_statement`.
 * El backend exige USUA_PERM_ADMIN nivel 3 y vuelve a leer la declaración.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

/**
 * @param {App.Locals} locals
 * @param {boolean} conCuerpo
 */
function headersDe(locals, conCuerpo) {
  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${locals.accessToken}`,
    "X-Tenant-Slug": locals.user?.tenant_slug ?? "",
  };
  if (locals.user?.sub) headers["X-User-Id"] = locals.user.sub;
  if (conCuerpo) headers["Content-Type"] = "application/json";
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

  /**
   * @type {{
   *   provider?: "local" | "ppi",
   *   send_metadata?: boolean,
   *   transmission_statement?: string,
   * }}
   */
  const payload = {};
  if (body?.provider === "local" || body?.provider === "ppi") payload.provider = body.provider;
  if (typeof body?.send_metadata === "boolean") payload.send_metadata = body.send_metadata;
  if (typeof body?.transmission_statement === "string" && body.transmission_statement.trim()) {
    payload.transmission_statement = body.transmission_statement.trim().slice(0, 4000);
  }
  if (payload.provider === undefined && payload.send_metadata === undefined) {
    return json(
      { error: "validation_error", detail: "Indique el proveedor o los metadatos." },
      { status: 400 },
    );
  }

  try {
    const res = await fetch(`${API_V1}/config/intelligence`, {
      method: "PUT",
      headers: headersDe(locals, true),
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo actualizar la configuración." },
      { status: 503 },
    );
  }
}
