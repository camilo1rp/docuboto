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
 * HALLAZGO DE DOMINIO (reportado, no corregido aquí): NINGUNO de estos tres
 * routers backend declara `require_permission` — a diferencia de
 * expediente-metadata/templates y trd (archive-service, USUA_PERM_TRD),
 * metadata/templates y metadata/elements (document-service) aceptan la
 * escritura de cualquier llamado autenticado del tenant, sin gate de
 * permiso. Esta pantalla NO inventa una autorización que el backend no
 * aplica (sería una falsa sensación de seguridad) — sí gatea sus propios
 * botones de escritura con USUA_PERM_ADMIN como medida conservadora
 * adicional (nunca ofrece MENOS de lo que el backend exige, aquí ofrece
 * más restricción de la que el backend exige, lo cual es seguro).
 *
 * También se trae la lista de series TRD y tipos documentales (solo
 * lectura, sin permiso) para poblar los selects de la plantilla
 * correspondiente sin obligar al admin a memorizar UUIDs/códigos.
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
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

  async function fetchList(path) {
    try {
      const res = await fetch(`${API_V1}${path}`, { headers });
      if (res.ok) return { items: await res.json().catch(() => []), error: null };
      const body = await res.json().catch(() => ({}));
      return { items: [], error: body.detail ?? `Error ${res.status}` };
    } catch {
      return { items: [], error: "No se pudo conectar al servidor." };
    }
  }

  const [expedienteTemplates, documentTemplates, elements, series, tipos] = await Promise.all([
    fetchList("/expediente-metadata/templates"),
    fetchList("/metadata/templates"),
    fetchList("/metadata/elements"),
    fetchList("/trd?size=200"),
    fetchList("/tipos-documentales"),
  ]);

  return {
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
