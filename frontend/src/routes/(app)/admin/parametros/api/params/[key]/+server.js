// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/parametros/api/params/[key] — Proxy SSR: leer/actualizar un
 * parámetro de tenant (D-05, E14) — GET/PUT /config/params/{key}.
 *
 * `value` es JSON arbitrario (dict/list/escalar) — se valida que el cuerpo
 * recibido parsee como JSON antes de reenviarlo (allowlist: solo `value`
 * viaja, nunca `key` del body — el key SIEMPRE viene de la ruta).
 *
 * HALLAZGO DE DOMINIO (ver +page.server.js): sin gate de permiso en el
 * backend; USUA_PERM_ADMIN aplicado solo del lado del cliente.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

function headersFor(locals, withContentType = false) {
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  /** @type {Record<string, string>} */
  const headers = { Authorization: `Bearer ${locals.accessToken}`, "X-Tenant-Slug": tenant };
  if (userId) headers["X-User-Id"] = userId;
  if (withContentType) headers["Content-Type"] = "application/json";
  return headers;
}

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, params }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  if (!params.key) {
    return json({ error: "validation_error", detail: "Clave de parámetro obligatoria." }, { status: 400 });
  }

  try {
    const res = await fetch(`${API_V1}/config/params/${encodeURIComponent(params.key)}`, { headers: headersFor(locals) });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json({ error: "connection_error", detail: "No se pudo conectar al servidor de parámetros." }, { status: 503 });
  }
}

/** @type {import('./$types').RequestHandler} */
export async function PUT({ locals, params, request }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  if (!params.key) {
    return json({ error: "validation_error", detail: "Clave de parámetro obligatoria." }, { status: 400 });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  if (!body || !("value" in body)) {
    return json({ error: "validation_error", detail: "El campo 'value' es obligatorio." }, { status: 400 });
  }

  try {
    const res = await fetch(`${API_V1}/config/params/${encodeURIComponent(params.key)}`, {
      method: "PUT",
      headers: headersFor(locals, true),
      body: JSON.stringify({ value: body.value }),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json({ error: "connection_error", detail: "No se pudo actualizar el parámetro." }, { status: 503 });
  }
}
