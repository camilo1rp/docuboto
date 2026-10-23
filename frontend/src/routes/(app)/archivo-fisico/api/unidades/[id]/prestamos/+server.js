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
 * POST → valida la unión discriminada `prestatario` (C-5/C11: interno por
 *         `user_id` = auth_users.id, o externo con nombre + documento), arma el
 *         payload campo a campo (nunca `prestado_por`: lo resuelve el servidor
 *         del bearer), reenvía a POST /unidades/{id}/prestamos
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";
import { construirPayloadPrestamo } from "$lib/utils/prestamo.js";

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

export async function GET({ locals, params, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const { id } = params;

  try {
    // Paginación (AREA-07-19): el backend pagina (`size` ≤ 100) y emite
    // `X-Total-Count`; se reenvían ambos para que la pantalla pueda decir «20 de
    // 25» y no presentar una ventana como si fuera el histórico entero.
    const q = new URLSearchParams();
    const page = url.searchParams.get("page");
    q.set("page", page && /^\d+$/.test(page) ? page : "1");
    const size = url.searchParams.get("size");
    q.set("size", size && /^\d+$/.test(size) ? size : "100");
    const res = await fetch(`${API_V1}/unidades/${id}/prestamos?${q.toString()}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
    });
    const data = await res.json().catch(() => ({}));
    const total = res.headers.get("X-Total-Count");
    return json(data, {
      status: res.status,
      headers: total ? { "X-Total-Count": total } : {},
    });
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

  const { prestatario, fecha_devolucion_esperada, observaciones } = body ?? {};

  const bad = (/** @type {string} */ detail) =>
    json({ error: "validation_error", detail }, { status: 422 });

  if (
    !fecha_devolucion_esperada ||
    typeof fecha_devolucion_esperada !== "string" ||
    !isValidDate(fecha_devolucion_esperada.trim())
  ) {
    return bad(
      "El campo 'fecha_devolucion_esperada' es obligatorio y debe tener formato YYYY-MM-DD.",
    );
  }

  const construido = construirPayloadPrestamo(
    prestatario,
    fecha_devolucion_esperada,
    observaciones,
  );
  if ("error" in construido) return bad(construido.error);

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
      body: JSON.stringify(construido.ok),
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
