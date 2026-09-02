// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin/plantillas/+page.server.js — Carga SSR de plantillas de contenido (T-12c).
 *
 * Carga la lista completa de plantillas (sin filtro de activo para que el admin
 * vea también las inactivas).
 *
 * El cuerpo HTML de cada plantilla se SANEA con `attachSafeCuerpo` (D-05, T-12c).
 * El campo `cuerpo_html` es el único que puede usarse con {@html} en la vista.
 *
 * Acceso: requiere USUA_PERM_ADMIN (el backend gatéa; la UI refleja el permiso).
 * El access_token NO se expone — las llamadas cliente usan /admin/plantillas/api/* (D-05).
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";
import { attachSafeCuerpo } from "$lib/server/sanitizeHtml.js";
import { mensajeError } from "$lib/utils/apiError.js";

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, fetch }) {
  const { user } = await parent();

  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";

  let plantillas = [];
  let dependencias = [];
  let loadError = null;

  const headers = { Authorization: `Bearer ${token}`, "X-Tenant-Slug": tenant };

  try {
    // `?todas=true` es OBLIGATORIO aquí y exige USUA_PERM_ADMIN en el backend.
    // Sin él, el listado se recorta por la dependencia del llamante (E23,
    // RF-PRO-06) y esta pantalla —cuyo trabajo es gestionar las plantillas de
    // TODA la entidad— dejaría fuera precisamente las que hay que asignar.
    const res = await fetch(`${API_V1}/plantillas?todas=true`, { headers });

    if (res.ok) {
      const raw = await res.json().catch(() => []);
      if (Array.isArray(raw)) {
        raw.forEach(attachSafeCuerpo);
        plantillas = raw;
      }
    } else {
      const body = await res.json().catch(() => ({}));
      loadError = mensajeError(body, `Error ${res.status}`);
    }
  } catch {
    loadError = "No se pudo conectar al servidor de plantillas.";
  }

  // Organigrama para el selector de asignación. Degrada a lista vacía: sin él
  // no se puede restringir una plantilla, pero sí crearla y editarla — un fallo
  // del catálogo no debe dejar la pantalla inservible.
  try {
    const res = await fetch(`${API_V1}/dependencias?activo=true`, { headers });
    if (res.ok) {
      const raw = await res.json().catch(() => []);
      if (Array.isArray(raw)) dependencias = raw;
    }
  } catch {
    dependencias = [];
  }

  return {
    plantillas,
    dependencias,
    loadError,
    // token y tenant NO se exponen: las llamadas cliente usan /admin/plantillas/api/* (D-05)
  };
}
