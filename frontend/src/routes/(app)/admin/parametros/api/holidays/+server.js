// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/parametros/api/holidays — Proxy SSR: calendario de festivos
 * (D-05, E14) — GET/POST /config/holidays.
 *
 * GET  ?year= → HolidayResponse[].
 * POST          → crea un festivo puntual. Body: { fecha, descripcion, tipo?,
 *   is_national? }.
 *
 * HALLAZGO DE DOMINIO (ver +page.server.js): sin gate de permiso en el
 * backend; USUA_PERM_ADMIN aplicado solo del lado del cliente.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

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
export async function GET({ locals, url }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const params = new URLSearchParams();
  const year = url.searchParams.get("year");
  if (year && /^\d{4}$/.test(year)) params.set("year", year);

  try {
    const res = await fetch(`${API_V1}/config/holidays?${params}`, { headers: headersFor(locals) });
    const data = await res.json().catch(() => []);
    return json(data, { status: res.status });
  } catch {
    return json({ error: "connection_error", detail: "No se pudo conectar al servidor de festivos." }, { status: 503 });
  }
}

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, request }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  const { fecha, descripcion, tipo, is_national } = body ?? {};
  if (typeof fecha !== "string" || !DATE_RE.test(fecha)) {
    return json({ error: "validation_error", detail: "La fecha debe tener formato YYYY-MM-DD." }, { status: 400 });
  }
  if (typeof descripcion !== "string" || !descripcion.trim()) {
    return json({ error: "validation_error", detail: "La descripción es obligatoria." }, { status: 400 });
  }

  const payload = {
    fecha,
    descripcion: descripcion.trim(),
    tipo: typeof tipo === "string" && tipo.trim() ? tipo.trim() : "festivo",
    is_national: is_national !== false,
  };

  try {
    const res = await fetch(`${API_V1}/config/holidays`, {
      method: "POST",
      headers: headersFor(locals, true),
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json({ error: "connection_error", detail: "No se pudo crear el festivo." }, { status: 503 });
  }
}
