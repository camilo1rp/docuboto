// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin/parametros/+page.server.js — Carga SSR de parámetros de tenant y
 * festivos (E14, Fase 5 cierre de desfase API↔UI) —
 * services/tenant-service/app/routers/config.py.
 *
 * GET /config/params (dict plano key→valor JSON) y GET /config/holidays
 * (SIN filtro de año: la calculadora de días hábiles necesita conocer todos
 * los festivos registrados para poder mostrar, con honestidad, cuáles caen
 * dentro de la ventana calculada — `BusinessDaysResult` del backend solo
 * devuelve la fecha resultante, no el desglose; el desglose se construye en
 * el cliente a partir de este mismo catálogo de festivos, ver +page.svelte).
 *
 * HALLAZGO DE DOMINIO: `services/tenant-service/app/routers/config.py`
 * declara explícitamente en su docstring "el gate de permiso admin del
 * llamante se añade cuando el JWT esté enriquecido — hoy el api-gateway
 * media el acceso" — es decir, NINGÚN endpoint de este router (ni params ni
 * festivos) exige un permiso hoy. Esta pantalla no inventa una autorización
 * que el backend no aplica; gatea sus propios botones de escritura con
 * USUA_PERM_ADMIN como medida conservadora del lado del cliente.
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, fetch }) {
  const { user } = await parent();

  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";
  const userId = user?.sub ?? "";

  /** @type {Record<string, string>} */
  const headers = { Authorization: `Bearer ${token}`, "X-Tenant-Slug": tenant };
  if (userId) headers["X-User-Id"] = userId;

  let params = {};
  let paramsError = null;
  try {
    const res = await fetch(`${API_V1}/config/params`, { headers });
    if (res.ok) {
      params = await res.json().catch(() => ({}));
    } else {
      const body = await res.json().catch(() => ({}));
      paramsError = body.detail ?? `Error ${res.status}`;
    }
  } catch {
    paramsError = "No se pudo conectar al servidor de parámetros.";
  }

  let holidays = [];
  let holidaysError = null;
  try {
    const res = await fetch(`${API_V1}/config/holidays`, { headers });
    if (res.ok) {
      holidays = await res.json().catch(() => []);
    } else {
      const body = await res.json().catch(() => ({}));
      holidaysError = body.detail ?? `Error ${res.status}`;
    }
  } catch {
    holidaysError = "No se pudo conectar al servidor de festivos.";
  }

  return { params, paramsError, holidays, holidaysError };
}
