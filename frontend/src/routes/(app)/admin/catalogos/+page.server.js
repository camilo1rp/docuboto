// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin/catalogos/+page.server.js — Carga SSR de los catálogos de
 * referencia por tenant (E14, Fase 5 cierre de desfase API↔UI).
 *
 * GET /catalogos (tenant-service) devuelve solo los NOMBRES de catálogo
 * (whitelist `CATALOGS` en services/tenant-service/app/repositories/
 * catalogos_repository.py) — hoy: tipos-identificacion, tipos-remitente,
 * medios-recepcion, tipos-anexo. Los ITEMS de cada catálogo se cargan bajo
 * demanda al seleccionarlo (maestro-detalle, ver +page.svelte), no todos de
 * una vez: son catálogos independientes y cargarlos todos en el `load`
 * multiplicaría llamadas SSR sin necesidad si el admin solo va a mirar uno.
 *
 * HALLAZGO DE DOMINIO: `services/tenant-service/app/routers/catalogos.py`
 * NO declara `require_permission` en ningún endpoint (a diferencia de TRD).
 * Esta pantalla NO inventa una autorización que el backend no aplica; gatea
 * sus propios botones de escritura con USUA_PERM_ADMIN como medida
 * conservadora del lado del cliente (ver +page.svelte).
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";
import { mensajeError } from "$lib/utils/apiError.js";

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, fetch }) {
  const { user } = await parent();

  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";
  const userId = user?.sub ?? "";

  /** @type {Record<string, string>} */
  const headers = { Authorization: `Bearer ${token}`, "X-Tenant-Slug": tenant };
  if (userId) headers["X-User-Id"] = userId;

  let catalogos = [];
  let loadError = null;
  try {
    const res = await fetch(`${API_V1}/catalogos`, { headers });
    if (res.ok) {
      catalogos = await res.json().catch(() => []);
    } else {
      const body = await res.json().catch(() => ({}));
      loadError = mensajeError(body, `Error ${res.status}`);
    }
  } catch {
    loadError = "No se pudo conectar al servidor de catálogos.";
  }

  return { catalogos, loadError };
}
