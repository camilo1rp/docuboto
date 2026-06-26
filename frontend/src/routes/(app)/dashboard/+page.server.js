// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * dashboard/+page.server.js — Carga SSR de métricas del dashboard (T-07, RF-UI-06).
 *
 * Endpoint real: GET /api/v1/reports/radicados  (document-service, E09)
 * Respuesta: ReportSummary { total, by_doc_type, by_status, by_month, by_dependencia }
 *
 * La carga es server-side para que las métricas lleguen pre-renderizadas al
 * navegador (no spinner en pantalla principal). Si el backend falla,
 * la vista degrada con `summary = null` y muestra EmptyState.
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, fetch }) {
  // Heredar user del layout — en particular tenant_slug y verificación de sesión.
  const { user } = await parent();

  const token = cookies.get(COOKIE_ACCESS_TOKEN);
  const tenant = user?.tenant_slug ?? "";

  let summary = null;
  let summaryError = null;

  try {
    const res = await fetch(`${API_V1}/reports/radicados`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
    });

    if (res.ok) {
      summary = await res.json();
    } else {
      const body = await res.json().catch(() => ({}));
      summaryError = body.detail ?? `Error ${res.status}`;
    }
  } catch (err) {
    // Gateway inalcanzable (contenedor apagado en dev, etc.)
    summaryError = "No se pudo conectar al servidor de reportes.";
  }

  return { summary, summaryError };
}
