// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /radicar/api/ingesta — Proxy SSR a la ingesta de preparación (archivo → proveedor).
 *
 * POST { file_id } crea el stub. El token va en la cookie httpOnly.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i;

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
  const fileId = typeof body?.file_id === "string" ? body.file_id.trim() : "";
  if (!UUID_RE.test(fileId)) {
    return json(
      { error: "validation_error", detail: "file_id debe ser un UUID." },
      { status: 422 },
    );
  }
  return _proxy(locals, `${API_V1}/knowledge/ingesta`, {
    method: "POST",
    body: JSON.stringify({ file_id: fileId }),
  });
}

/**
 * @param {{ accessToken?: string, user?: { tenant_slug?: string, sub?: string } }} locals
 * @param {string} url
 * @param {RequestInit} extra
 */
async function _proxy(locals, url, extra) {
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${locals.accessToken ?? ""}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;
  try {
    const res = await fetch(url, { ...extra, headers });
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
