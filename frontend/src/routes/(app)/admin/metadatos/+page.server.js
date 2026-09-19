// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin/metadatos/+page.server.js — Carga SSR de plantillas y elementos de
 * metadatos (E03/ADR-007, Fase 5 cierre de desfase API↔UI).
 *
 * Reparto con /admin/trd (para no duplicar la misma edición en dos sitios):
 *   - /admin/trd administra la CLASIFICACIÓN (series TRD/CCD, tipos
 *     documentales) — el ESQUELETO.
 *   - /admin/metadatos administra los CAMPOS de esos esquemas — el JSON
 *     Schema (`json_schema`) que valida los metadatos al radicar/abrir
 *     expediente, y el catálogo de elementos reutilizables.
 *
 * Tres catálogos, tres servicios distintos:
 *   - GET /expediente-metadata/templates (archive-service) — plantilla por
 *     serie TRD (trd_serie_id). Solo alta + lectura (sin PATCH/DELETE en el
 *     backend: nueva versión = nuevo registro, ver +page.svelte).
 *   - GET /metadata/templates (document-service) — plantilla por tipo
 *     documental (tipo_documental, string libre — no hay FK real con
 *     tipos_documentales.code de archive-service, dos servicios
 *     independientes). Mismo patrón alta-only.
 *   - GET /metadata/elements (document-service) — elementos de metadato
 *     reutilizables (clave/etiqueta/tipo_dato/...), CRUD completo.
 *
 * Gate real (verificado en código, revisión UX 2026-08-02 punto 2, lote 1):
 * los TRES routers exigen `USUA_PERM_TRD` en sus mutaciones — igual que
 * expediente-metadata/templates y trd (archive-service). `metadata/templates`
 * y `metadata/elements` (document-service) declaran
 * `require_permission("USUA_PERM_TRD")` en create/update/delete. Esta
 * pantalla gatea sus botones de escritura con USUA_PERM_TRD (ver
 * +page.svelte), no con USUA_PERM_ADMIN.
 *
 * También se trae la lista de series TRD y tipos documentales (solo
 * lectura, sin permiso) para poblar los selects de la plantilla
 * correspondiente sin obligar al admin a memorizar UUIDs/códigos.
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";
import { mensajeError } from "$lib/utils/apiError.js";
import { traerListaCompleta } from "$lib/server/listaCompleta.server.js";

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, fetch }) {
  const { user } = await parent();

  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";
  const userId = user?.sub ?? "";

  /** @type {Record<string, string>} */
  const headers = { Authorization: `Bearer ${token}`, "X-Tenant-Slug": tenant };
  if (userId) headers["X-User-Id"] = userId;

  async function fetchList(path) {
    try {
      const res = await fetch(`${API_V1}${path}`, { headers });
      if (res.ok) return { items: await res.json().catch(() => []), error: null };
      const body = await res.json().catch(() => ({}));
      return { items: [], error: mensajeError(body, `Error ${res.status}`) };
    } catch {
      return { items: [], error: "No se pudo conectar al servidor." };
    }
  }

  const [expedienteTemplates, documentTemplates, elements, series, tipos] = await Promise.all([
    fetchList("/expediente-metadata/templates"),
    fetchList("/metadata/templates"),
    fetchList("/metadata/elements"),
    // Las series alimentan el desplegable de clasificación: si la lista llega
    // recortada, la serie que falte no se puede elegir — y no se ve como una
    // lista corta, se ve como que esa serie no existe. `size=200` era el TOPE
    // del backend, no una holgura.
    traerListaCompleta(fetch, `${API_V1}/trd`, headers, mensajeError),
    fetchList("/tipos-documentales"),
  ]);

  return {
    seriesIncompletas: series.incompleta ?? false,
    seriesTotal: series.total ?? series.items.length,
    expedienteTemplates: expedienteTemplates.items,
    expedienteTemplatesError: expedienteTemplates.error,
    documentTemplates: documentTemplates.items,
    documentTemplatesError: documentTemplates.error,
    elements: elements.items,
    elementsError: elements.error,
    series: series.items,
    tipos: tipos.items,
  };
}
