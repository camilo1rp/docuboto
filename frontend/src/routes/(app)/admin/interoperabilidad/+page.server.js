// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin/interoperabilidad/+page.server.js — Catálogo de dependencias activas
 * para la carga masiva de documentos: el lote identifica la dependencia por
 * CÓDIGO (`dest_dept_code`/`origin_dept_code`, ADR-032), no por nombre libre.
 * Si el catálogo falla, la página lo dice (no se teclea un código a ciegas).
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, fetch }) {
  const { user } = await parent();
  const headers = {
    Authorization: `Bearer ${cookies.get(COOKIE_ACCESS_TOKEN) ?? ""}`,
    "X-Tenant-Slug": user?.tenant_slug ?? "",
  };
  let dependencias = [];
  let dependenciasError = false;
  try {
    const res = await fetch(`${API_V1}/dependencias?activo=true`, { headers });
    if (res.ok) {
      const raw = await res.json().catch(() => []);
      dependencias = Array.isArray(raw) ? raw : [];
    } else {
      dependenciasError = true;
    }
  } catch {
    dependenciasError = true;
  }
  return { dependencias, dependenciasError };
}
