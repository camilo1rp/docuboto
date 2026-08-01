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
      code: body.error ?? body.detail?.error ?? "upstream_error",
      detail: body.detail?.detail ?? body.detail ?? `HTTP ${res.status}`,
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

  const [planResult, tableroResult] = await Promise.allSettled([
    gatewayGet(`/pinar/planes/${id}`, token, tenant, extraHeaders),
    gatewayGet(`/pinar/planes/${id}/tablero`, token, tenant, extraHeaders),
  ]);

  if (planResult.status === "rejected") {
    const err = planResult.reason;
    if (err?.status === 404) {
      error(404, { message: `Plan PINAR ${id} no encontrado` });
    }
    if (err?.status === 403) {
      error(403, { message: "Sin permisos para ver este plan PINAR" });
    }
    error(500, { message: err?.detail ?? "Error al cargar el plan PINAR" });
  }

  const tablero =
    tableroResult.status === "fulfilled"
      ? { data: tableroResult.value, error: null }
      : {
          data: null,
          error: {
            code: tableroResult.reason?.code ?? "unknown_error",
            detail: tableroResult.reason?.detail ?? "No se pudo cargar el tablero.",
            status: tableroResult.reason?.status ?? 500,
          },
        };

  return {
    plan: planResult.value,
    tablero,
  };
}
