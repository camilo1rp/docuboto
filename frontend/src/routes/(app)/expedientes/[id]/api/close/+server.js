// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /expedientes/[id]/api/close — Endpoint de servidor para cierre de expediente (D-05, ADR-011).
 *
 * El token y el userId se leen de event.locals (cookie httpOnly + claims del JWT).
 * El JS del cliente nunca ve el token; el backend re-valida RBAC y clearance.
 *
 * POST → cierra el expediente (open → closed).
 *   Requiere X-User-Id para trazabilidad y acotación por clearance (E02 §9).
 *   Respuesta: ExpedienteResponse actualizado o { ok: true } si el gateway devuelve 204.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  const { id } = params;

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/expedientes/${id}/close`, {
      method: "POST",
      headers,
    });

    // Algunos gateways devuelven 204 sin cuerpo
    if (res.status === 204) {
      return json({ ok: true }, { status: 200 });
    }

    const data = await res.json().catch(() => ({ ok: true }));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo cerrar el expediente." },
      { status: 503 },
    );
  }
}
