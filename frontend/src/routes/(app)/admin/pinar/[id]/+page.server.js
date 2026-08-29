// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin/pinar/[id]/+page.server.js — Workspace de un plan PINAR (RF-ADM-08,
 * Ac. AGN 003/2015, Fase 7 cierre de desfase API↔UI).
 *
 * Dos facetas, en paralelo (Promise.allSettled): el fallo del tablero no
 * tumba la vista del plan (patrón ya usado en expedientes/[id]/+page.server.js).
 *   1. Plan       → GET /pinar/planes/{id}                    — CRÍTICA
 *   2. Tablero    → GET /pinar/planes/{id}/tablero             — no crítica
 *
 * El tablero puede legítimamente estar vacío (plan recién creado, sin
 * objetivos/proyectos todavía) — eso NO es un error de carga. Se distingue
 * explícitamente `tablero.error` (fallo real, ej. red/5xx) de un tablero con
 * arrays vacíos (estado vacío legítimo): la plantilla nunca pinta un tablero
 * en cero cuando en realidad no pudo cargar.
 *
 * Las acciones del ciclo de vida (aprobar/ejecutar/cerrar) usan los proxies
 * locales /admin/pinar/api/planes/[id]/* (D-05); el access_token no se
 * expone en `data`.
 */

import { error } from "@sveltejs/kit";
import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";
import { codigoError, mensajeError } from "$lib/utils/apiError.js";

/**
 * @param {string} path
 * @param {string} token
 * @param {string} tenant
 * @param {Record<string,string>} [extraHeaders]
 */
async function gatewayGet(path, token, tenant, extraHeaders = {}) {
  const res = await fetch(`${API_V1}${path}`, {
    headers: {
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
      detail: mensajeError(body.detail, mensajeError(body, `HTTP ${res.status}`)),
      status: res.status,
    };
  }

  return res.json();
}

export async function load({ params, cookies, parent }) {
  const { id } = params;
  const { user } = await parent();
  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";
  const userId = user?.sub ?? "";
  const extraHeaders = userId ? { "X-User-Id": userId } : {};

  if (!id) {
    error(400, { message: "ID de plan PINAR requerido" });
  }

  // El CONTENIDO del plan (Fase 8, 2026-08-29): aspectos críticos con su
  // priorización, objetivos, proyectos e instrumentos articulados. Va en el
  // mismo `allSettled` y NO en cargas bajo demanda por pestaña: son seis
  // consultas pequeñas al mismo servicio, y pedirlas al abrir cada pestaña
  // convertiría un cambio de pestaña en una espera — además de multiplicar los
  // estados de carga que la plantilla tendría que distinguir.
  //
  // Todas son NO CRÍTICAS, igual que el tablero: el fallo de una no puede
  // tumbar la vista del plan.
  const [
    planResult,
    tableroResult,
    ejesResult,
    aspectosResult,
    priorizacionResult,
    objetivosResult,
    proyectosResult,
    instrumentosResult,
  ] = await Promise.allSettled([
    gatewayGet(`/pinar/planes/${id}`, token, tenant, extraHeaders),
    gatewayGet(`/pinar/planes/${id}/tablero`, token, tenant, extraHeaders),
    gatewayGet(`/pinar/ejes`, token, tenant, extraHeaders),
    gatewayGet(`/pinar/planes/${id}/aspectos`, token, tenant, extraHeaders),
    gatewayGet(`/pinar/planes/${id}/priorizacion`, token, tenant, extraHeaders),
    gatewayGet(`/pinar/planes/${id}/objetivos`, token, tenant, extraHeaders),
    gatewayGet(`/pinar/planes/${id}/proyectos`, token, tenant, extraHeaders),
    gatewayGet(`/pinar/planes/${id}/instrumentos`, token, tenant, extraHeaders),
  ]);

  if (planResult.status === "rejected") {
    const err = planResult.reason;
    if (err?.status === 404) {
      error(404, { message: `Plan PINAR ${id} no encontrado` });
    }
    if (err?.status === 403) {
      error(403, { message: "Sin permisos para ver este plan PINAR" });
    }
    error(500, { message: mensajeError(err, "Error al cargar el plan PINAR") });
  }

  const tablero =
    tableroResult.status === "fulfilled"
      ? { data: tableroResult.value, error: null }
      : {
          data: null,
          error: {
            code: tableroResult.reason?.code ?? "unknown_error",
            detail: mensajeError(tableroResult.reason, "No se pudo cargar el tablero."),
            status: tableroResult.reason?.status ?? 500,
          },
        };

  // Una lista vacía y un fallo de carga NO se confunden: `contenidoError`
  // recoge el primero que falle. Sin esto, un 5xx se leería como «este plan no
  // tiene objetivos», que es justo la lectura que ha tenido esta pantalla
  // desde que existe — solo que por no consumir los endpoints.
  const listas = {
    ejes: ejesResult,
    aspectos: aspectosResult,
    priorizacion: priorizacionResult,
    objetivos: objetivosResult,
    proyectos: proyectosResult,
    instrumentos: instrumentosResult,
  };
  /** @type {Record<string, any[]>} */
  const contenido = {};
  let contenidoError = null;
  for (const [clave, resultado] of Object.entries(listas)) {
    if (resultado.status === "fulfilled" && Array.isArray(resultado.value)) {
      contenido[clave] = resultado.value;
    } else {
      contenido[clave] = [];
      contenidoError ??= mensajeError(
        resultado.status === "rejected" ? resultado.reason : null,
        `No se pudo cargar «${clave}» del plan.`,
      );
    }
  }

  // Las seis listas se devuelven NOMBRADAS y no con un `...contenido`: el
  // spread de un `Record` deja al inferidor de tipos sin las claves, y
  // `svelte-check` empieza a decir que `data.aspectos` no existe justo donde
  // más falta hace que avise.
  return {
    plan: planResult.value,
    tablero,
    ejes: contenido.ejes,
    aspectos: contenido.aspectos,
    priorizacion: contenido.priorizacion,
    objetivos: contenido.objetivos,
    proyectos: contenido.proyectos,
    instrumentos: contenido.instrumentos,
    contenidoError,
  };
}
