// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin/notificaciones/+page.server.js — Carga SSR de dos facetas
 * independientes (`services/notification-service/app/routers/`):
 *
 *   - Historial de envíos: `GET /notifications/history?limit=` — USUA_PERM_ADMIN.
 *     NO pagina con `X-Total-Count`: `notification_service.list_recent` solo
 *     acepta `limit` (1..100) y devuelve las N más recientes, sin total ni
 *     cursor. La pantalla lo declara explícitamente (no hay forma honesta de
 *     mostrar "1–50 de X" cuando el backend no sabe X).
 *   - Webhooks salientes: `GET /webhooks` (CRUD completo) — USUA_PERM_ADMIN.
 *     Sin paginación en el backend (config de administración, universo
 *     pequeño); la cabecera `X-Total-Count` sí existe pero es el total
 *     crudo (no hay `page`/`size` en la ruta).
 *
 * Las dos facetas se cargan en paralelo y cada una lleva su PROPIO estado de
 * error — un fallo en `/notifications/history` no debe impedir ver los
 * webhooks ya cargados, ni viceversa (nunca un error global que oculte lo
 * que sí funcionó).
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";
import { mensajeError } from "$lib/utils/apiError.js";

const HISTORY_LIMIT = 50;

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

  const [historyResult, webhooksResult] = await Promise.allSettled([
    fetch(`${API_V1}/notifications/history?limit=${HISTORY_LIMIT}`, { headers }),
    fetch(`${API_V1}/webhooks`, { headers }),
  ]);

  let history = [];
  let historyError = null;
  if (historyResult.status === "fulfilled" && historyResult.value.ok) {
    const raw = await historyResult.value.json().catch(() => []);
    history = Array.isArray(raw) ? raw : [];
  } else if (historyResult.status === "fulfilled") {
    const body = await historyResult.value.json().catch(() => ({}));
    historyError = mensajeError(body, `Error ${historyResult.value.status}`);
  } else {
    historyError = "No se pudo conectar al servidor de notificaciones.";
  }

  let webhooks = [];
  let webhooksError = null;
  if (webhooksResult.status === "fulfilled" && webhooksResult.value.ok) {
    const raw = await webhooksResult.value.json().catch(() => []);
    webhooks = Array.isArray(raw) ? raw : [];
  } else if (webhooksResult.status === "fulfilled") {
    const body = await webhooksResult.value.json().catch(() => ({}));
    webhooksError = mensajeError(body, `Error ${webhooksResult.value.status}`);
  } else {
    webhooksError = "No se pudo conectar al servidor de webhooks.";
  }

  return {
    history,
    historyError,
    historyLimit: HISTORY_LIMIT,
    webhooks,
    webhooksError,
  };
}
