// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/parametros/api/business-days — Proxy SSR: calculadora de días
 * hábiles (D-05, E14) — GET /business-days/calculate.
 *
 * GET ?from=YYYY-MM-DD&days=N → BusinessDaysResult { from_date, business_days,
 *   due_date }. El backend NO devuelve el desglose de festivos/fines de
 *   semana descontados — solo la fecha resultante. La pantalla reconstruye
 *   ese desglose en el cliente a partir de `data.holidays` (cargado en
 *   +page.server.js, sin filtro de año) enumerando el rango [from, due_date]
 *   — es información derivada de datos reales, no inventada (ver
 *   +page.svelte). No se permite `days` negativo (RF-RAD-04 no tiene sentido
 *   con plazos negativos) ni fuera del rango que el backend acepta (0..3650).
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, url }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  const from = url.searchParams.get("from");
  const daysRaw = url.searchParams.get("days");
  const days = Number(daysRaw);

  if (!from || !DATE_RE.test(from)) {
    return json(
      { error: "validation_error", detail: "El parámetro 'from' debe tener formato YYYY-MM-DD." },
      { status: 400 },
    );
  }
  if (!Number.isInteger(days) || days < 0 || days > 3650) {
    return json(
      { error: "validation_error", detail: "El número de días hábiles debe estar entre 0 y 3650." },
      { status: 400 },
    );
  }

  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  /** @type {Record<string, string>} */
  const headers = { Authorization: `Bearer ${locals.accessToken}`, "X-Tenant-Slug": tenant };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const params = new URLSearchParams({ from, days: String(days) });
    const res = await fetch(`${API_V1}/business-days/calculate?${params}`, { headers });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo calcular la fecha de vencimiento." },
      { status: 503 },
    );
  }
}
