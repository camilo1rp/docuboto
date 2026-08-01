// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/colas/api/[service] — Proxy SSR: listado de dead-letters (DLQ,
 * ADR-021 Fase 5) de UNO de los tres servicios que exponen la cola
 * (`admin_deadletter.py` en workflow-service, notification-service y
 * signature-service). `service` está en una allowlist cerrada — nunca se
 * interpola libremente en la URL del gateway (evita construir un proxy
 * abierto a rutas arbitrarias).
 *
 * Gate REAL del backend: `PERM_DLQ_ADMIN` (NO `USUA_PERM_ADMIN` — verificado
 * leyendo los 3 routers). El listado exige además `X-User-Id` vía
 * `get_actor` (401 si falta).
 *
 * Cabeceras reenviadas intactas:
 *   - X-Total-Count → total POST-filtro de clearance (no el total crudo).
 *   - X-Truncated   → "true" cuando `list_scoped_for_clearance` alcanzó el
 *     cap de candidatas (`DEADLETTER_CANDIDATE_CAP`); en ese caso las filas
 *     pendientes más antiguas pueden no estar en el listado y el total puede
 *     sub-reportar. La pantalla DEBE mostrar esto, no solo saberlo el código.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const SERVICE_PATHS = {
  workflow: "workflow/admin/deadletter",
  notification: "notification/admin/deadletter",
  signature: "signature/admin/deadletter",
};

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, params, url }) {
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

  const searchParams = new URLSearchParams();
  searchParams.set("status", url.searchParams.get("status") ?? "pending");
  const originStream = url.searchParams.get("origin_stream");
  if (originStream) searchParams.set("origin_stream", originStream);
  searchParams.set("page", url.searchParams.get("page") ?? "1");
  searchParams.set("size", url.searchParams.get("size") ?? "20");

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/${servicePath}/?${searchParams}`, { headers });
    const data = await res.json().catch(() => []);
    return json(data, {
      status: res.status,
      headers: {
        "X-Total-Count": res.headers.get("X-Total-Count") ?? "0",
        "X-Truncated": res.headers.get("X-Truncated") ?? "false",
      },
    });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de colas de errores." },
      { status: 503 },
    );
  }
}
