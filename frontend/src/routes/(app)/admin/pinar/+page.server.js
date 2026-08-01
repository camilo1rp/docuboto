// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin/pinar/+page.server.js — Carga SSR del listado de planes PINAR
 * (RF-ADM-08, Ac. AGN 003/2015, Fase 7 cierre de desfase API↔UI).
 *
 * PINAR tenía 30 endpoints en tenant-service y ninguna pantalla: era
 * inservible desde la aplicación. Este MVP cubre listado + detalle con
 * ciclo de vida (borrador→aprobado→en_ejecucion→cerrado) + tablero. El
 * resto (aspectos críticos, priorización, objetivos, proyectos, seguimiento,
 * instrumentos, mapa de ruta) queda para una fase posterior.
 *
 * GET /pinar/planes — solo autenticado en el backend (consulta); la gestión
 * (crear/aprobar/ejecutar/cerrar) exige USUA_PERM_ADMIN, gateado en la UI
 * contra el permiso real del usuario ($permissions), nunca contra el rol.
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";

const ESTADOS = new Set(["borrador", "aprobado", "en_ejecucion", "cerrado"]);

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, fetch, url }) {
  const { user } = await parent();

  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";
  const userId = user?.sub ?? "";

  /** @type {Record<string, string>} */
  const headers = { Authorization: `Bearer ${token}`, "X-Tenant-Slug": tenant };
  if (userId) headers["X-User-Id"] = userId;

  const estado = url.searchParams.get("estado");
  const params = new URLSearchParams();
  if (estado && ESTADOS.has(estado)) params.set("estado", estado);

  let planes = [];
  let loadError = null;
  try {
    const qs = params.toString();
    const res = await fetch(`${API_V1}/pinar/planes${qs ? `?${qs}` : ""}`, { headers });
    if (res.ok) {
      planes = await res.json().catch(() => []);
    } else {
      const body = await res.json().catch(() => ({}));
      loadError = body.detail ?? `Error ${res.status}`;
    }
  } catch {
    loadError = "No se pudo conectar al servidor del PINAR.";
  }

  return { planes, loadError, estadoFiltro: estado && ESTADOS.has(estado) ? estado : "" };
}
