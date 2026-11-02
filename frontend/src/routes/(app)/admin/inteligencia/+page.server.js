// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin/inteligencia/+page.server.js — Estado de la pasarela (ADR-045 §4).
 *
 * Tres lecturas, todas con USUA_PERM_ADMIN nivel 3:
 *   GET /config/intelligence
 *   GET /config/intelligence/capabilities
 *   GET /config/intelligence/inventory  (solo en local)
 *
 * Si la declaración o el inventario no se pueden leer, la pantalla lo dice.
 * No se sustituye por una lista vacía ni por un cero.
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";
import { mensajeError } from "$lib/utils/apiError.js";

/**
 * @param {import('@sveltejs/kit').ServerLoadEvent["cookies"]} cookies
 * @param {{tenant_slug?: string, sub?: string} | null | undefined} user
 */
function headersDe(cookies, user) {
  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": user?.tenant_slug ?? "",
  };
  if (user?.sub) headers["X-User-Id"] = user.sub;
  return headers;
}

/**
 * @param {typeof fetch} fetch
 * @param {string} ruta
 * @param {Record<string, string>} headers
 * @param {string} fallo
 */
async function leer(fetch, ruta, headers, fallo) {
  try {
    const res = await fetch(`${API_V1}${ruta}`, { headers });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) return { cuerpo: null, error: mensajeError(body, fallo) };
    return { cuerpo: body, error: null };
  } catch {
    return { cuerpo: null, error: fallo };
  }
}

/** @type {import('./$types').PageServerLoad} */
export async function load({ cookies, parent, fetch }) {
  const { user } = await parent();
  const headers = headersDe(cookies, user);

  const config = await leer(
    fetch,
    "/config/intelligence",
    headers,
    "No se pudo leer la configuración de inteligencia.",
  );
  const capabilities = await leer(
    fetch,
    "/config/intelligence/capabilities",
    headers,
    "No se pudo leer la declaración del proveedor.",
  );

  const enLocal = config.cuerpo?.provider === "local";
  const inventory = enLocal
    ? await leer(
        fetch,
        "/config/intelligence/inventory",
        headers,
        "No se pudo leer cuántos radicados conserva el proveedor.",
      )
    : { cuerpo: null, error: null };

  return {
    config: config.cuerpo,
    configError: config.error,
    capabilities: capabilities.cuerpo,
    capabilitiesError: capabilities.error,
    inventory: inventory.cuerpo,
    inventoryError: inventory.error,
  };
}
