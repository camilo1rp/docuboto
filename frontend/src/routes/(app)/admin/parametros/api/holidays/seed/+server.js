// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/parametros/api/holidays/seed — Proxy SSR: precargar festivos
 * colombianos calculados (D-05, E14) — POST /config/holidays/seed.
 *
 * Body: { year_from, year_to } — ambos años inclusivos, calculados con el
 * algoritmo de Pascua + reglas Emiliani (services/tenant-service/app/core/
 * calendar.py, sin BD). El backend valida year_to >= year_from (400 si no).
 *
 * HALLAZGO DE DOMINIO (ver +page.server.js): sin gate de permiso en el
 * backend; USUA_PERM_ADMIN aplicado solo del lado del cliente.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

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

  const { year_from, year_to } = body ?? {};
  const yearFrom = Number(year_from);
  const yearTo = Number(year_to);
  if (
    !Number.isInteger(yearFrom) ||
    !Number.isInteger(yearTo) ||
    yearFrom < 2000 ||
    yearTo > 2100 ||
    yearTo < yearFrom
  ) {
    return json({ error: "validation_error", detail: "Rango de años inválido." }, { status: 400 });
  }

  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${locals.accessToken}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/config/holidays/seed`, {
      method: "POST",
      headers,
      body: JSON.stringify({ year_from: yearFrom, year_to: yearTo }),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo precargar los festivos." },
      { status: 503 },
    );
  }
}
