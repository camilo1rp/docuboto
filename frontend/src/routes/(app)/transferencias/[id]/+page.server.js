// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * transferencias/[id]/+page.server.js — Carga SSR del detalle de una
 * transferencia documental, con su acta de entrega en apariencia de
 * certificado (Fase 4, cierre de desfase API↔UI, E12/E17/F4).
 *
 * Tres llamadas SSR independientes (`Promise.allSettled`, faceta por
 * faceta — nunca un error global): la transferencia (siempre debería
 * existir si se navegó desde el listado), el acta CONGELADA (404 legítimo
 * si aún no se recibió) y la verificación agregada de firmas (sello +
 * personales — costosa, se pide explícitamente aquí en la carga inicial
 * SOLO si el acta existe, para pintar el certificado completo de una vez;
 * refrescos posteriores tras firmar van por `/transferencias/api/*`).
 *
 * `verify=true` en el acta NO se pide en la carga inicial (cómputo caro de
 * cripto+revocación bajo demanda) — el usuario lo dispara con el botón
 * "Verificar sello".
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
export async function load({ cookies, parent, params, fetch }) {
  const { user } = await parent();
  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";
  const { id } = params;

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };

  const doFetch = (path) => fetch(`${API_V1}${path}`, { headers });

  const transferenciaResult = await Promise.allSettled([
    fetchJson(doFetch, `/transferencias/${encodeURIComponent(id)}`),
  ]);
  const transferencia = settle(transferenciaResult[0]);

  // El acta y su verificación solo tienen sentido si la transferencia existe
  // y está (o estuvo) recibida — pero se piden en paralelo siempre: un 404
  // de "aún no hay acta" es una respuesta legítima, no un error que deba
  // bloquear el resto de la carga (patrón faceta-por-faceta).
  const [actaResult, verificacionResult] = await Promise.allSettled([
    fetchJson(doFetch, `/transferencias/${encodeURIComponent(id)}/acta`),
    fetchJson(doFetch, `/transferencias/${encodeURIComponent(id)}/acta/verificacion`),
  ]);

  return {
    transferencia,
    acta: settle(actaResult),
    verificacion: settle(verificacionResult),
  };
}
