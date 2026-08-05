// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/colas/api/[service]/[eventId] — Proxy SSR: detalle de un
 * dead-letter (`GET .../deadletter/{event_id}`, PERM_DLQ_ADMIN, D-05).
 *
 * 404 aquí es AMBIGUO por diseño del backend (no-read-up, RF-SEG-08): el
 * mismo cuerpo se devuelve tanto si el evento no existe como si existe pero
 * el llamante no tiene clearance suficiente sobre el radicado referenciado.
 * Este proxy NO intenta distinguir los dos casos (sería reconstruir en el
 * cliente la información que el backend deliberadamente oculta) — se limita
 * a propagar el 404 tal cual.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const SERVICE_PATHS = {
  workflow: "workflow/admin/deadletter",
  notification: "notification/admin/deadletter",
  signature: "signature/admin/deadletter",
};

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, params }) {
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
    const res = await fetch(`${API_V1}/${servicePath}/${encodeURIComponent(params.eventId)}`, {
      headers,
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de colas de errores." },
      { status: 503 },
    );
  }
}
