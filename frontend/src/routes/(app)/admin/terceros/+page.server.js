// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/terceros — carga inicial del directorio (RF-RAD-05).
 *
 * Trae dos cosas y por separado, con `allSettled`: si la detección de
 * duplicados falla, el directorio se sigue viendo. Fundir las dos llamadas
 * haría que un fallo en la consulta más cara —la de agrupación— dejara la
 * pantalla entera en blanco.
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { mensajeError } from "$lib/utils/apiError.js";
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

  const pedir = async (/** @type {string} */ ruta) => {
    const res = await fetch(`${API_V1}${ruta}`, { headers });
    const cuerpo = await res.json().catch(() => null);
    if (!res.ok) throw new Error(mensajeError(cuerpo, `Error ${res.status}`));
    return cuerpo;
  };

  const [dir, dup] = await Promise.allSettled([
    pedir("/terceros?limit=100"),
    pedir("/terceros/duplicados?limit=50"),
  ]);

  return {
    terceros: dir.status === "fulfilled" ? (dir.value ?? []) : [],
    terceroError: dir.status === "rejected" ? String(dir.reason?.message ?? dir.reason) : null,
    duplicados: dup.status === "fulfilled" ? (dup.value ?? []) : [],
    duplicadoError: dup.status === "rejected" ? String(dup.reason?.message ?? dup.reason) : null,
  };
}
