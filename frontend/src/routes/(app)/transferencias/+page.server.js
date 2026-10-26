// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
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
import { codigoError, mensajeError } from "$lib/utils/apiError.js";
import { anotarCodigoExpediente } from "$lib/server/transferenciaEtiquetas.js";

/** Estados y tipos que el backend admite (E12). Allowlist de la semilla de URL. */
const ESTADOS_VALIDOS = ["preparada", "enviada", "recibida", "rechazada"];
const TIPOS_VALIDOS = ["primaria", "secundaria"];

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, url }) {
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
        detail: mensajeError(result.reason, "Error desconocido"),
        status: result.reason?.status ?? 500,
      },
    };
  }

  /** GET simple al gateway; devuelve `null` si falla (faceta no crítica). */
  async function opcional(ruta) {
    try {
      const res = await fetch(`${API_V1}${ruta}`, {
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
          "X-Tenant-Slug": tenant,
        },
      });
      if (!res.ok) return null;
      return await res.json();
    } catch {
      return null;
    }
  }

  // Filtros SEMBRADOS DESDE LA URL. `/transferencias?estado=enviada` es el
  // enlace que el panel principal y los avisos ya producían, y la pantalla los
  // ignoraba: pintaba la lista COMPLETA con los desplegables en «todos», así
  // que quien llegaba por el enlace veía otra cosa de la que pidió y sin
  // ninguna señal de que el filtro se hubiera perdido. Se resuelven en el
  // servidor —no al montar— para que la primera pintada ya venga filtrada.
  // Allowlist, no paso directo: un valor inventado en la URL se descarta en vez
  // de viajar al gateway y volver como un 422.
  const estadoSemilla = ESTADOS_VALIDOS.includes(url.searchParams.get("estado") ?? "")
    ? (url.searchParams.get("estado") ?? "")
    : "";
  const tipoSemilla = TIPOS_VALIDOS.includes(url.searchParams.get("tipo") ?? "")
    ? (url.searchParams.get("tipo") ?? "")
    : "";
  const consulta = new URLSearchParams();
  if (estadoSemilla) consulta.set("estado", estadoSemilla);
  if (tipoSemilla) consulta.set("tipo", tipoSemilla);
  const sufijo = consulta.toString() ? `?${consulta}` : "";

  const [transferenciasResult, ubicacionesResult] = await Promise.allSettled([
    (async () => {
      const res = await fetch(`${API_V1}/transferencias${sufijo}`, {
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
          code: codigoError(body) ?? "upstream_error",
          detail: mensajeError(body, `HTTP ${res.status}`),
          status: res.status,
        };
      }
      return res.json();
    })(),
    // Catálogo de ubicaciones para los desplegables de origen/destino. El
    // formulario pedía el UUID escrito a mano —un identificador que ninguna
    // pantalla del sistema muestra—, así que registrar una transferencia
    // obligaba a abrir la base de datos. Mismo defecto que se corrigió en
    // `/archivo-fisico`, que había quedado aquí sin corregir.
    // No es crítica: si falla, el modal degrada a campo de texto.
    opcional("/ubicaciones?activo=true&size=200"),
  ]);

  const ubicacionesCrudas =
    ubicacionesResult.status === "fulfilled" ? ubicacionesResult.value : null;
  // El listado puede venir envuelto (`{items: […]}`) o como lista pelada según
  // el endpoint; se aceptan las dos formas en vez de suponer una.
  const ubicaciones = Array.isArray(ubicacionesCrudas?.items)
    ? ubicacionesCrudas.items
    : Array.isArray(ubicacionesCrudas)
      ? ubicacionesCrudas
      : [];

  // Misma anotación que hace el proxy `/transferencias/api` — la carga inicial
  // NO pasa por él (va directa al gateway), así que sin esto la primera pintada
  // saldría con UUID y solo se arreglaría al recargar la lista. Se comparte el
  // helper para que las dos rutas no puedan divergir.
  const transferencias = settle(transferenciasResult);
  if (transferencias.data) {
    transferencias.data = await anotarCodigoExpediente(transferencias.data, (id) =>
      opcional(`/expedientes/${encodeURIComponent(id)}`),
    );
  }

  return {
    transferencias,
    ubicaciones,
    // Semillas de los desplegables: la pantalla arranca mostrando el filtro que
    // de verdad se aplicó, no «todos» sobre una lista recortada.
    filtros: { estado: estadoSemilla, tipo: tipoSemilla },
    // token y tenant NO se exponen: las acciones usan /transferencias/api/* (D-05)
  };
}
