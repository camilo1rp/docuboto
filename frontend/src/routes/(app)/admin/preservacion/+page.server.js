// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
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
 * de retención (Conservación Total). Quinta, solo USUA_PERM_ADMIN >= 3: sellos de
 * archivo (C-6).
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

  // La cola de pendientes se pide aparte de las demás facetas porque de su
  // respuesta interesa también una CABECERA, no solo el cuerpo: cuántos
  // índices acabó sellando el job en vez del acto del cierre. Sin ese número,
  // una cola vacía se lee como «nunca ha fallado nada», y durante dos días
  // significó «el compensador está reparando todos los cierres».
  const pendientesFirmaPromise = (async () => {
    const res = await doFetch("/expedientes/indices/pendientes-firma?page=1&size=20");
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
    const crudo = res.headers.get("X-Sellado-Diferido-Count");
    const selladoDiferido = crudo !== null && crudo !== "" ? Number(crudo) : null;
    return {
      items: await res.json(),
      // `null` y `0` NO son lo mismo y no deben pintarse igual: `null` es «el
      // gateway no reenvió la cabecera o el backend no la emite», y decir «0»
      // ahí sería afirmar que nunca hizo falta el compensador sin saberlo.
      selladoDiferido: Number.isFinite(selladoDiferido) ? selladoDiferido : null,
    };
  })();

  const [planResult, versionsResult, pendientesFirmaResult, pendientesRenovacionResult] =
    await Promise.allSettled([
      fetchJson(doFetch, "/preservacion/plan"),
      fetchJson(doFetch, "/preservacion/plan/versions"),
      pendientesFirmaPromise,
      fetchJson(doFetch, "/expedientes/indices/pendientes-renovacion?page=1&size=20"),
    ]);

  const pendientesFirma = settle(pendientesFirmaResult);

  // Sellos de archivo (C-6, ADR-042): solo con USUA_PERM_ADMIN >= 3, que es lo
  // que el backend exige (403 sin él). Sin el nivel NO se pide: la faceta ni
  // aparece, en vez de pintar un 403 que la persona no puede resolver.
  const veSellos = (user?.permission_levels?.USUA_PERM_ADMIN ?? 0) >= 3;
  /** @type {any} */
  let sellos = null;
  if (veSellos) {
    const [resumenR, listaR] = await Promise.allSettled([
      fetchJson(doFetch, "/signature/admin/sellos-archivo/resumen"),
      (async () => {
        const res = await doFetch("/signature/admin/sellos-archivo?page=1&size=20");
        if (!res.ok) {
          const body = await res.json().catch(() => ({}));
          throw {
            code: codigoError(body) ?? "upstream_error",
            detail: mensajeError(body, `HTTP ${res.status}`),
            status: res.status,
          };
        }
        const total = Number(res.headers.get("X-Total-Count"));
        return { items: await res.json(), total: Number.isFinite(total) ? total : null };
      })(),
    ]);
    sellos = { resumen: settle(resumenR), lista: settle(listaR) };
  }

  return {
    plan: settle(planResult),
    versions: settle(versionsResult),
    pendientesFirma: {
      data: pendientesFirma.data?.items ?? null,
      error: pendientesFirma.error,
    },
    selladoDiferido: pendientesFirma.data?.selladoDiferido ?? null,
    pendientesRenovacion: settle(pendientesRenovacionResult),
    // `null` = la persona no tiene el nivel: la faceta no se ofrece.
    sellos,
  };
}
