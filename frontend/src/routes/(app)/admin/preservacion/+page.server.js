// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin/preservacion/+page.server.js — Carga SSR de la administración de
 * preservación digital (Fase 4, cierre de desfase API↔UI, E10).
 *
 * Cuatro facetas INDEPENDIENTES (`Promise.allSettled`, nunca un error
 * global): plan vigente, historial de versiones del plan, índices
 * pendientes de sellado XAdES, y artefactos WORM pendientes de renovación
 * de retención (Conservación Total).
 *
 * Endpoints reales verificados en `services/storage-service/app/routers/
 * preservacion.py` y `services/archive-service/app/routers/indice.py` — NO
 * se incluyen aquí el empaquetado manual de AIP (`POST /preservacion/aip`)
 * ni la protección WORM manual (`POST /preservacion/proteger-artefacto`):
 * ambos requieren un `file_id`/inventario de documentos que solo
 * archive-service conoce (y que esta pantalla, fuera del contexto de un
 * expediente concreto, no puede reconstruir sin inventar datos) — ver la
 * nota completa en `+page.svelte`. El AIP y los eventos PREMIS de UN
 * expediente concreto ya se muestran en la pestaña "Preservación" del
 * workspace de expediente (`/expedientes/[id]`).
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";
import { codigoError, mensajeError } from "$lib/utils/apiError.js";

/** @param {PromiseSettledResult<any>} result */
function settle(result) {
  if (result.status === "fulfilled") {
    return { data: result.value, error: null };
  }
  return {
    data: null,
    error: {
      code: result.reason?.code ?? "unknown_error",
      detail: mensajeError(result.reason, "Error desconocido"),
      status: result.reason?.status ?? 500,
    },
  };
}

/**
 * @param {(path: string) => Promise<Response>} doFetch
 * @param {string} path
 */
async function fetchJson(doFetch, path) {
  const res = await doFetch(path);
  if (!res.ok) {
    const body = await res.json().catch(() => ({
      error: "upstream_error",
      detail: `HTTP ${res.status}`,
      status: res.status,
    }));
    throw {
      code: codigoError(body) ?? "upstream_error",
      detail: mensajeError(body, `HTTP ${res.status}`),
      status: res.status,
    };
  }
  return res.json();
}

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, fetch }) {
  const { user } = await parent();
  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";
  const userId = user?.sub ?? "";

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  const doFetch = (path) => fetch(`${API_V1}${path}`, { headers });

  const [planResult, versionsResult, pendientesFirmaResult, pendientesRenovacionResult] =
    await Promise.allSettled([
      fetchJson(doFetch, "/preservacion/plan"),
      fetchJson(doFetch, "/preservacion/plan/versions"),
      fetchJson(doFetch, "/expedientes/indices/pendientes-firma?page=1&size=20"),
      fetchJson(doFetch, "/expedientes/indices/pendientes-renovacion?page=1&size=20"),
    ]);

  return {
    plan: settle(planResult),
    versions: settle(versionsResult),
    pendientesFirma: settle(pendientesFirmaResult),
    pendientesRenovacion: settle(pendientesRenovacionResult),
  };
}
