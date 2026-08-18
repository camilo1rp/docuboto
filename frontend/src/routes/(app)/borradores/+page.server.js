// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * borradores/+page.server.js — Carga SSR inicial de borradores y plantillas (T-12c).
 *
 * Carga en paralelo:
 *   1. GET /api/v1/borradores?estado= — lista de borradores filtrada por estado
 *      (por defecto sin filtro — muestra todos).
 *   2. GET /api/v1/plantillas?activo=true — plantillas activas para el selector
 *      del formulario de creación de borrador.
 *
 * El cuerpo HTML (campo `cuerpo`) de cada borrador/plantilla se SANEA server-side
 * con `attachSafeCuerpo` antes de entregarse al cliente (D-05, T-12c).
 * El campo resultante `cuerpo_html` es el único que debe usarse con {@html} en la vista.
 *
 * El access_token NO se expone en $page.data — las llamadas cliente usan
 * /borradores/api[/id/accion] que leen locals.accessToken en el servidor (D-05).
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";
import { attachSafeCuerpo } from "$lib/server/sanitizeHtml.js";
import { mensajeError } from "$lib/utils/apiError.js";

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, url, fetch }) {
  const { user } = await parent();

  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";

  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };

  // Filtro de estado desde query param (deep-link friendly)
  const estadoFilter = url.searchParams.get("estado") ?? "";

  const borradoresParams = new URLSearchParams();
  if (estadoFilter) borradoresParams.set("estado", estadoFilter);

  const [borradoresRes, plantillasRes] = await Promise.allSettled([
    fetch(`${API_V1}/borradores?${borradoresParams.toString()}`, { headers }),
    fetch(`${API_V1}/plantillas?activo=true`, { headers }),
  ]);

  // ---------------------------------------------------------------------------
  // Borradores
  // ---------------------------------------------------------------------------
  let borradores = [];
  let loadError = null;

  if (borradoresRes.status === "fulfilled" && borradoresRes.value.ok) {
    const raw = await borradoresRes.value.json().catch(() => []);
    if (Array.isArray(raw)) {
      raw.forEach(attachSafeCuerpo);
      borradores = raw;
    }
  } else if (borradoresRes.status === "fulfilled") {
    const body = await borradoresRes.value.json().catch(() => ({}));
    loadError = mensajeError(body, `Error ${borradoresRes.value.status}`);
  } else {
    loadError = "No se pudo conectar al servidor de borradores.";
  }

  // ---------------------------------------------------------------------------
  // Plantillas (para el selector de borrador; degradación elegante si falla)
  // ---------------------------------------------------------------------------
  let plantillas = [];

  if (plantillasRes.status === "fulfilled" && plantillasRes.value.ok) {
    const raw = await plantillasRes.value.json().catch(() => []);
    if (Array.isArray(raw)) {
      raw.forEach(attachSafeCuerpo);
      plantillas = raw;
    }
  }

  return {
    borradores,
    plantillas,
    loadError,
    initialEstado: estadoFilter,
    // token y tenant NO se exponen: las llamadas cliente usan /borradores/api/* (D-05)
  };
}
