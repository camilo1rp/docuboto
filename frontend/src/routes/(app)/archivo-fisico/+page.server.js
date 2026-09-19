// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * archivo-fisico/+page.server.js — Carga SSR inicial del módulo de archivo físico (T-12, E17).
 *
 * Composición de facetas vía SSR con Promise.allSettled:
 *   1. Ubicaciones raíz  → GET /api/v1/ubicaciones?activo=true   (sin parent_id = raíz)
 *   2. Préstamos activos → GET /api/v1/prestamos?estado=prestado
 *   3. Préstamos vencidos→ GET /api/v1/prestamos?estado=vencido
 *
 * El fallo de una faceta no tumba la vista completa — cada faceta expone su
 * propio estado de error (ADR-011 / spec E22 §R4).
 *
 * El access_token NO se expone en $page.data — nunca alcanza el JS del cliente (D-05).
 * Las acciones cliente usan /archivo-fisico/api/* que leen la cookie httpOnly.
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";
import { codigoError, mensajeError } from "$lib/utils/apiError.js";
import { traerListaCompleta } from "$lib/server/listaCompleta.server.js";

// ---------------------------------------------------------------------------
// Helper: fetch autenticado GET desde el servidor
// ---------------------------------------------------------------------------

/**
 * Realiza una petición GET autenticada al gateway desde el servidor.
 * Lanza un objeto estructurado { code, detail, status } en caso de error.
 *
 * @param {string} path   — ruta relativa, ej. '/ubicaciones'
 * @param {string} token
 * @param {string} tenant
 * @param {Record<string,string>} [extraHeaders]
 * @returns {Promise<any>}
 */
async function gatewayGet(path, token, tenant, extraHeaders = {}) {
  const res = await fetch(`${API_V1}${path}`, {
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      "X-Tenant-Slug": tenant,
      ...extraHeaders,
    },
  });

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

// ---------------------------------------------------------------------------
// load — composición SSR paralela
// ---------------------------------------------------------------------------

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, fetch }) {
  const { user } = await parent();
  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";

  /** Las mismas que arma `gatewayGet`, para la faceta que no pasa por él. */
  const cabeceras = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };

  // Facetas en paralelo — allSettled: el fallo de una no tumba las demás
  const [
    ubicacionesResult,
    prestamosActivosResult,
    prestamosVencidosResult,
    catalogoUbicacionesResult,
    catalogoSeriesResult,
  ] = await Promise.allSettled([
    // Faceta 1: primer nivel del árbol. `solo_raiz=true` es OBLIGATORIO:
    // omitir `parent_id` significa "no filtres por padre", no "las raíces"
    // — con la lectura contraria cada depósito y cada gaveta aparecía
    // también como raíz y el árbol dejaba de ser un árbol.
    gatewayGet("/ubicaciones?activo=true&solo_raiz=true", token, tenant),

    // Faceta 2: Préstamos activos
    gatewayGet("/prestamos?estado=prestado", token, tenant),

    // Faceta 3: Préstamos vencidos
    gatewayGet("/prestamos?estado=vencido", token, tenant),

    // Faceta 4: CATÁLOGO de ubicaciones — el árbol entero, plano y con su
    // ruta. No es una duplicación de la faceta 1: aquella pinta el primer
    // nivel, esta puebla los desplegables donde antes había que escribir a
    // mano el UUID de la ubicación (que ninguna pantalla del sistema
    // muestra, así que el módulo era inoperable sin abrir la base de datos).
    gatewayGet("/ubicaciones?activo=true", token, tenant),

    // Faceta 5: series TRD, para el filtro del FUID. Mismo motivo que la
    // anterior: el filtro pedía el UUID de la serie escrito a mano.
    //
    // Se traen TODAS las páginas: `size=200` era el tope del backend y no una
    // holgura, así que la serie que no entrara en el corte no se podía elegir en
    // el filtro — indistinguible de que no exista.
    traerListaCompleta(fetch, `${API_V1}/trd?is_active=true`, cabeceras, mensajeError).then(
      (r) => r.items,
    ),
  ]);

  // -------------------------------------------------------------------------
  // Serializar resultados — cada faceta expone { data, error }
  // -------------------------------------------------------------------------

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

  return {
    facetas: {
      ubicaciones: settle(ubicacionesResult),
      prestamosActivos: settle(prestamosActivosResult),
      prestamosVencidos: settle(prestamosVencidosResult),
      catalogoUbicaciones: settle(catalogoUbicacionesResult),
      catalogoSeries: settle(catalogoSeriesResult),
    },
    // token, tenant y userId NO se exponen: las acciones cliente usan /archivo-fisico/api/* (D-05)
  };
}
