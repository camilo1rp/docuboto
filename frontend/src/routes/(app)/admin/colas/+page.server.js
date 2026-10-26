// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin/colas/+page.server.js — Carga SSR de la primera página de la cola de
 * errores (dead-letter, ADR-021 Fase 5) del servicio por defecto
 * ("workflow"), estado "pending". El selector de servicio/estado del cliente
 * refetch contra `admin/colas/api/[service]` (D-05).
 *
 * PERM_DLQ_ADMIN (NO USUA_PERM_ADMIN) es el gate real de los 3 routers
 * `admin_deadletter.py` (workflow-service, notification-service,
 * signature-service) — verificado leyendo el código, no asumido.
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";
import { mensajeError } from "$lib/utils/apiError.js";

const DEFAULT_SERVICE = "workflow";
const SERVICE_PATHS = {
  workflow: "workflow/admin/deadletter",
  notification: "notification/admin/deadletter",
  signature: "signature/admin/deadletter",
};
const PAGE_SIZE = 20;

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, fetch }) {
  const { user } = await parent();

  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";
  const userId = user?.sub ?? "";

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  let items = [];
  let totalCount = 0;
  let loadError = null;

  try {
    const res = await fetch(
      `${API_V1}/${SERVICE_PATHS[DEFAULT_SERVICE]}/?status=pending&page=1&size=${PAGE_SIZE}`,
      { headers },
    );
    if (res.ok) {
      const raw = await res.json().catch(() => []);
      items = Array.isArray(raw) ? raw : [];
      totalCount = Number(res.headers.get("X-Total-Count") ?? items.length);
    } else {
      const body = await res.json().catch(() => ({}));
      loadError = mensajeError(body, `Error ${res.status}`);
    }
  } catch {
    loadError = "No se pudo conectar al servidor de colas de errores.";
  }

  return {
    defaultService: DEFAULT_SERVICE,
    items,
    totalCount,
    pageSize: PAGE_SIZE,
    loadError,
  };
}
