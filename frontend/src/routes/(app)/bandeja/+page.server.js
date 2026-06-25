// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * bandeja/+page.server.js — Carga SSR de la primera página de radicados (T-08, RF-UI-03).
 *
 * Endpoint real: GET /api/v1/documents?doc_type=&status=&page=&size=
 *   - Filtros soportados por el backend: doc_type (E|S|I), status, page, size
 *   - Paginación vía X-Total-Count (header de respuesta)
 *   - Filtros de fecha y dependencia disponibles en /api/v1/search (T-11)
 *
 * La carga inicial es SSR (page=1, sin filtros). Los cambios de filtro y paginación
 * subsiguientes se hacen en el cliente reutilizando document.service.js.
 *
 * Expone token + tenant al cliente para que pueda hacer llamadas paginadas/filtradas
 * sin volver al servidor. NOTA: el token se pasa como dato público de página
 * SOLO porque se requiere para las llamadas cliente; nunca se incluye en el bundle.
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.js";

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, url, fetch }) {
  const { user } = await parent();

  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";

  // Leer filtros iniciales de query params (deep-link friendly)
  const docType = url.searchParams.get("doc_type") ?? "";
  const status = url.searchParams.get("status") ?? "";
  const page = Math.max(1, Number(url.searchParams.get("page") ?? 1));
  const size = 20;

  const params = new URLSearchParams({ page: String(page), size: String(size) });
  if (docType) params.set("doc_type", docType);
  if (status) params.set("status", status);

  let documents = [];
  let total = 0;
  let loadError = null;

  try {
    const res = await fetch(`${API_V1}/documents?${params.toString()}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
    });

    if (res.ok) {
      total = Number(res.headers.get("X-Total-Count") ?? 0);
      documents = await res.json();
    } else {
      const body = await res.json().catch(() => ({}));
      loadError = body.detail ?? `Error ${res.status}`;
    }
  } catch {
    loadError = "No se pudo conectar al servidor de documentos.";
  }

  return {
    documents,
    total,
    loadError,
    // Pasar contexto de sesión al cliente para llamadas paginadas/filtradas
    token,
    tenant,
    // Filtros activos al cargar
    initialFilters: { docType, status, page },
  };
}
