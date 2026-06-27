// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * transferencias/+page.server.js — Carga SSR inicial del módulo de transferencias (T-12, E12).
 *
 * Carga inicial: GET /api/v1/transferencias (sin filtros, todos los estados).
 * Los filtros dinámicos y acciones del cliente usan /transferencias/api/* (D-05, ADR-011).
 *
 * El access_token NO se expone en $page.data — nunca alcanza el JS del cliente.
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent }) {
  const { user } = await parent();
  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";

  /** @param {PromiseSettledResult<any>} result */
  function settle(result) {
    if (result.status === "fulfilled") {
      return { data: result.value, error: null };
    }
    return {
      data: null,
      error: {
        code: result.reason?.code ?? "unknown_error",
        detail: result.reason?.detail ?? "Error desconocido",
        status: result.reason?.status ?? 500,
      },
    };
  }

  const [transferenciasResult] = await Promise.allSettled([
    (async () => {
      const res = await fetch(`${API_V1}/transferencias`, {
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
          "X-Tenant-Slug": tenant,
        },
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({
          error: "upstream_error",
          detail: `HTTP ${res.status}`,
          status: res.status,
        }));
        throw {
          code: body.error ?? "upstream_error",
          detail: body.detail ?? `HTTP ${res.status}`,
          status: res.status,
        };
      }
      return res.json();
    })(),
  ]);

  return {
    transferencias: settle(transferenciasResult),
    // token y tenant NO se exponen: las acciones usan /transferencias/api/* (D-05)
  };
}
