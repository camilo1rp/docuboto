// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * reportes/+page.server.js — Carga SSR del resumen de reportes de radicados (T-11).
 *
 * Endpoint real: GET /api/v1/reports/radicados
 *   - Parámetros opcionales: date_from, date_to (ISO date)
 *   - Devuelve: { by_type, by_status, by_month, by_dept } con conteos.
 *
 * La descarga CSV usa el endpoint local /reportes/api/csv (proxy SSR),
 * no esta carga SSR — el botón apunta a ese endpoint con los mismos params.
 *
 * Pestaña "Índice de clasificados" (E08, Ley 1712 art. 20, Fase 4 cierre
 * desfase API↔UI): esta carga SOLO expone `canSeeIndice` (espejo del gate
 * real `PERM_RECLASIFICAR` de GET /reports/indice-reservado) — la UI no
 * ofrece la pestaña si el backend la rechazaría. El contenido del índice se
 * pide de forma perezosa desde el cliente al activar la pestaña (mismo
 * patrón que las facetas bajo demanda del workspace de expediente), vía
 * /reportes/api/indice-reservado — no se precarga aquí para no penalizar la
 * carga inicial de quien nunca abre esa pestaña.
 *
 * El access_token NO se expone en $page.data — nunca alcanza el JS del cliente.
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";
import { mensajeError } from "$lib/utils/apiError.js";

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, url }) {
  const { user } = await parent();

  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";
  // X-User-Id: inyectar para que quede activo cuando el backend de reportes aplique clearance
  const userId = user?.sub ?? "";

  // Filtros de rango de fechas (deep-link friendly)
  const dateFrom = url.searchParams.get("date_from") ?? "";
  const dateTo = url.searchParams.get("date_to") ?? "";

  const params = new URLSearchParams();
  if (dateFrom) params.set("date_from", dateFrom);
  if (dateTo) params.set("date_to", dateTo);
  const qs = params.toString();

  let summary = null;
  let loadError = null;

  try {
    const res = await fetch(`${API_V1}/reports/radicados${qs ? `?${qs}` : ""}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
        "X-User-Id": userId,
      },
    });

    if (res.ok) {
      summary = await res.json();
    } else {
      const body = await res.json().catch(() => ({}));
      loadError = mensajeError(body, `Error ${res.status}`);
    }
  } catch {
    loadError = "No se pudo conectar al servidor de reportes.";
  }

  // Catálogo para traducir el código de dependencia (ADR-032: el reporte agrupa
  // por `dest_dept_code`) a su nombre vigente. Si falla, se muestra el código.
  let dependencias = [];
  try {
    const depRes = await fetch(`${API_V1}/dependencias`, {
      headers: { Authorization: `Bearer ${token}`, "X-Tenant-Slug": tenant },
    });
    if (depRes.ok) {
      const raw = await depRes.json().catch(() => []);
      dependencias = Array.isArray(raw) ? raw : [];
    }
  } catch {
    dependencias = [];
  }

  return {
    summary,
    loadError,
    dependencias,
    // Filtros activos para inicializar el formulario y construir la URL del CSV
    initialFilters: { dateFrom, dateTo },
    // Espejo del gate real del backend (PERM_RECLASIFICAR) — controla si la
    // pestaña "Índice de clasificados" se ofrece; el backend re-valida igual.
    canSeeIndice: (user?.permissions ?? []).includes("PERM_RECLASIFICAR"),
    // token NO se expone: el botón CSV llama a /reportes/api/csv (D-05)
  };
}
