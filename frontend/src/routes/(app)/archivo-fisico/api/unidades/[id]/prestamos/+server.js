// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /archivo-fisico/api/unidades/[id]/prestamos — Proxy SSR de préstamos de unidad (D-05, E17).
 *
 * GET  → reenvía a GET /unidades/{id}/prestamos
 * POST → valida (prestado_a req, fecha_devolucion_esperada req YYYY-MM-DD),
 *         inyecta X-User-Id, reenvía a POST /unidades/{id}/prestamos
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
/**
 * Valida que una cadena sea una fecha calendario real en formato YYYY-MM-DD.
 * DATE_RE solo valida formato (acepta 2024-13-45); Date.parse() re-valida que
 * la fecha exista en el calendario. El backend re-valida igualmente, pero
 * rechazamos aquí las más obvias para ahorrar un round-trip.
 * @param {string} s
 */
function isValidDate(s) {
  if (!DATE_RE.test(s)) return false;
  const ts = Date.parse(s);
  return !Number.isNaN(ts);
}

export async function GET({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const { id } = params;

  try {
    const res = await fetch(`${API_V1}/unidades/${id}/prestamos`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo obtener los préstamos de la unidad." },
      { status: 503 },
    );
  }
}

export async function POST({ locals, params, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  const { id } = params;

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  const { prestado_a, prestado_por, fecha_devolucion_esperada } = body ?? {};

  if (!prestado_a || typeof prestado_a !== "string" || !prestado_a.trim()) {
    return json(
      { error: "validation_error", detail: "El campo 'prestado_a' es obligatorio." },
      { status: 422 },
    );
  }
  if (
    !fecha_devolucion_esperada ||
    typeof fecha_devolucion_esperada !== "string" ||
    !isValidDate(fecha_devolucion_esperada.trim())
  ) {
    return json(
      {
        error: "validation_error",
        detail:
          "El campo 'fecha_devolucion_esperada' es obligatorio y debe tener formato YYYY-MM-DD.",
      },
      { status: 422 },
    );
  }

  /** @type {Record<string, unknown>} */
  const payload = {
    prestado_a: prestado_a.trim(),
    fecha_devolucion_esperada: fecha_devolucion_esperada.trim(),
  };
  if (prestado_por && typeof prestado_por === "string" && prestado_por.trim()) {
    payload.prestado_por = prestado_por.trim();
  }

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/unidades/${id}/prestamos`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });
    if (res.status === 204) {
      return json({ ok: true }, { status: 200 });
    }
    const data = await res.json().catch(() => ({ ok: true }));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo registrar el préstamo." },
      { status: 503 },
    );
  }
}
