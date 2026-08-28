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
 * GET /pinar/planes — exige USUA_PERM_ADMIN en el backend desde 2026-08-28,
 * igual que la gestión (crear/aprobar/ejecutar/cerrar). La frase que había aquí
 * («solo autenticado en el backend, consulta») se borra con el hueco: describía
 * una apertura que la barra lateral y las tarjetas de /admin ya contradecían, y
 * un comentario obsoleto es el vector, no el adorno — quien lo lea creerá que
 * puede consumir esto sin permiso.
 *
 * Por eso el 403 NO viaja como `loadError` genérico: se distingue en
 * `sinPermiso`, y la pantalla lo dice con su nombre. Presentar una negativa de
 * acceso como un fallo de carga manda a buscar la avería donde no está — el
 * mismo defecto que /envios presentando un 403 como «no hay registros».
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";
import { mensajeError } from "$lib/utils/apiError.js";

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
  let sinPermiso = false;
  try {
    const qs = params.toString();
    const res = await fetch(`${API_V1}/pinar/planes${qs ? `?${qs}` : ""}`, { headers });
    if (res.ok) {
      planes = await res.json().catch(() => []);
    } else if (res.status === 403) {
      sinPermiso = true;
    } else {
      const body = await res.json().catch(() => ({}));
      loadError = mensajeError(body, `Error ${res.status}`);
    }
  } catch {
    loadError = "No se pudo conectar al servidor del PINAR.";
  }

  return {
    planes,
    loadError,
    sinPermiso,
    estadoFiltro: estado && ESTADOS.has(estado) ? estado : "",
  };
}
