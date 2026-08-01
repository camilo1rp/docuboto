// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/colas/api/[service]/[eventId]/replay — Proxy SSR: reprocesa un
 * dead-letter pendiente (`POST .../deadletter/{event_id}/replay`,
 * PERM_DLQ_ADMIN, D-05). Sin body — el backend re-ejecuta el handler
 * original con el sobre ya almacenado.
 *
 * Posibles respuestas de negocio que el cliente debe distinguir (no todas
 * son "error genérico"):
 *   - 502 replay_failed    → el reproceso falló, el evento sigue pending.
 *   - 409 already_resolved → otro admin ya lo resolvió (carrera).
 *   - 422 tenant_mismatch / unknown_origin_stream → dato del propio dead-letter.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const SERVICE_PATHS = {
  workflow: "workflow/admin/deadletter",
  notification: "notification/admin/deadletter",
  signature: "signature/admin/deadletter",
};

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  // `Object.hasOwn` y no un acceso directo: `SERVICE_PATHS["constructor"]` (o
  // "toString") devuelve un miembro heredado de `Object.prototype` en vez de
  // `undefined`, con lo que pasaría el guard de abajo y se interpolaría en la
  // URL del gateway. No hay traversal, pero el guard dejaría de ser la lista
  // cerrada que aparenta ser.
  const servicePath = Object.hasOwn(SERVICE_PATHS, params.service)
    ? SERVICE_PATHS[params.service]
    : undefined;
  if (!servicePath) {
    return json(
      { error: "unknown_service", detail: `Servicio de cola desconocido: ${params.service}` },
      { status: 400 },
    );
  }

  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(
      `${API_V1}/${servicePath}/${encodeURIComponent(params.eventId)}/replay`,
      { method: "POST", headers },
    );
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de colas de errores." },
      { status: 503 },
    );
  }
}
