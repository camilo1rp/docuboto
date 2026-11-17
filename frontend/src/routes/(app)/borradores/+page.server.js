// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
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
 *   3. GET /api/v1/tipos-documentales?activo=true — catálogo para el modal de radicar.
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

  const [borradoresRes, plantillasRes, dependenciasRes, tiposRes] = await Promise.allSettled([
    fetch(`${API_V1}/borradores?${borradoresParams.toString()}`, { headers }),
    fetch(`${API_V1}/plantillas?activo=true`, { headers }),
    fetch(`${API_V1}/dependencias?activo=true`, { headers }),
    fetch(`${API_V1}/tipos-documentales?activo=true`, { headers }),
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

  // Catálogo de dependencias activas: el destino se elige y se envía por CÓDIGO
  // (ADR-032). Degrada a lista vacía si falla (el selector lo dirá).
  let dependencias = [];
  let dependenciasError = null;
  if (dependenciasRes.status === "fulfilled" && dependenciasRes.value.ok) {
    const raw = await dependenciasRes.value.json().catch(() => []);
    if (Array.isArray(raw)) dependencias = raw;
  } else {
    dependenciasError =
      "No se pudo cargar el catálogo de dependencias: no podrá elegir el destino hasta recargar la página.";
  }

  // Catálogo de tipos documentales: se elige al radicar (Ac. AGN 060/2001 §5.2).
  // Si falla se DICE: un catálogo vacío por error no puede leerse como «no hay tipos».
  let tiposDocumentales = [];
  let tiposError = null;
  if (tiposRes.status === "fulfilled" && tiposRes.value.ok) {
    const raw = await tiposRes.value.json().catch(() => []);
    if (Array.isArray(raw)) tiposDocumentales = raw;
  } else {
    tiposError =
      "No se pudo cargar el catálogo de tipos documentales: no podrá radicar borradores hasta recargar la página.";
  }

  return {
    borradores,
    dependencias,
    dependenciasError,
    tiposDocumentales,
    tiposError,
    plantillas,
    loadError,
    initialEstado: estadoFilter,
    // token y tenant NO se exponen: las llamadas cliente usan /borradores/api/* (D-05)
  };
}
