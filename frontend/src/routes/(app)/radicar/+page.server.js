// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * radicar/+page.server.js — Carga SSR de catálogos para el stepper de radicación (T-09).
 *
 * Carga en paralelo los tres catálogos que necesita el stepper:
 *   1. GET /api/v1/dependencias?activo=true
 *        — lista de dependencias activas del tenant (tenant-service)
 *        — usadas en paso 2 (metadatos: destino/origen) y paso 5 (flujo/destino)
 *
 *   2. GET /api/v1/trd?is_active=true&size=100
 *        — series TRD activas del tenant (archive-service)
 *        — usadas en paso 4 (clasificación TRD/CCD)
 *
 *   3. GET /api/v1/tipos-documentales?activo=true
 *        — catálogo de tipos documentales (archive-service)
 *        — usados en paso 1 para asociar al tipo E/S/I y en paso 2
 *
 * Si algún catálogo falla, se devuelve vacío y el stepper lo maneja con
 * estados de error en el paso correspondiente (degradación elegante).
 *
 * Las llamadas POST de creación y subida de anexos usan los endpoints locales
 * /radicar/api/documents y /radicar/api/storage que leen la cookie httpOnly (D-05).
 * El access_token NO se expone en $page.data.
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, fetch }) {
  const { user } = await parent();

  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";

  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };

  // Carga paralela de los tres catálogos
  const [depRes, trdRes, tiposRes] = await Promise.allSettled([
    fetch(`${API_V1}/dependencias?activo=true`, { headers }),
    fetch(`${API_V1}/trd?is_active=true&size=100`, { headers }),
    fetch(`${API_V1}/tipos-documentales?activo=true`, { headers }),
  ]);

  // ---------------------------------------------------------------------------
  // Dependencias
  // ---------------------------------------------------------------------------
  let dependencias = [];
  if (depRes.status === "fulfilled" && depRes.value.ok) {
    dependencias = await depRes.value.json().catch(() => []);
  }

  // ---------------------------------------------------------------------------
  // Series TRD
  // ---------------------------------------------------------------------------
  let trdSeries = [];
  if (trdRes.status === "fulfilled" && trdRes.value.ok) {
    trdSeries = await trdRes.value.json().catch(() => []);
  }

  // ---------------------------------------------------------------------------
  // Tipos documentales
  // ---------------------------------------------------------------------------
  let tiposDocumentales = [];
  if (tiposRes.status === "fulfilled" && tiposRes.value.ok) {
    tiposDocumentales = await tiposRes.value.json().catch(() => []);
  }

  return {
    user,
    dependencias,
    trdSeries,
    tiposDocumentales,
    // token y tenant NO se exponen: las llamadas cliente usan /radicar/api/* (D-05)
  };
}
