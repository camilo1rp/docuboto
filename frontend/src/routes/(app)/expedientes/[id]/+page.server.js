// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Workspace de expediente — SSR paralelo (T-10, RF-UI-05).
 *
 * Composición de facetas vía SSR:
 *   Las llamadas al gateway se realizan EN PARALELO con Promise.allSettled,
 *   de modo que el fallo de una faceta no tumba toda la vista — cada faceta
 *   expone su propio estado de error (ADR-011 / spec E22 §R4).
 *
 * Facetas:
 *   1. Resumen     → GET /api/v1/expedientes/{id}           (expediente + radicados[])
 *   2. Índice E15  → GET /api/v1/expedientes/{id}/indice    (IndiceResponse)
 *   3. Eventos     → GET /api/v1/expedientes/{id}/eventos   (hoja de ruta append-only, E02 §9)
 *   4. Ubicación   → GET /api/v1/expedientes/{id}/unidades  (UnidadDeExpediente[], E17)
 *
 * Nota de anexos (E07):
 *   Los anexos de cada radicado están embebidos en expediente.radicados[].
 *   No existe un endpoint "listar todos los anexos de un expediente" en el backend;
 *   se construye iterando expediente.radicados[].anexos[] en el cliente.
 *   Para descargar un archivo (D-02, RF-SEG-08): la descarga directa a
 *   storage-service quedó cerrada (retirada del gateway). Ahora la MEDIA
 *   document-service, con no-read-up contra el nivel_seguridad del radicado
 *   dueño del anexo (`radicado_id`):
 *     GET /api/v1/documents/{document_id}/anexos/{file_id}/download
 *
 * Nota de flujo/historial por radicado:
 *   El workflow-service expone GET /api/v1/workflows/{radicado_id}/events (por radicado,
 *   no por expediente), exige X-User-Id y filtra por clearance. No existe un endpoint
 *   de "flujo del expediente" — la pestaña Flujo/Historial muestra los eventos del
 *   expediente (hoja de ruta E02 §9). Los eventos de flujo por radicado se cargan
 *   on-demand desde la bandeja de cada radicado.
 *
 * Las acciones del cliente (cerrar/transferir, descargar XML/anexos) usan los
 * endpoints locales /expedientes/[id]/api/* que leen la cookie httpOnly (D-05).
 * El access_token NO se expone en $page.data.
 */

import { error } from "@sveltejs/kit";
import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";

// ---------------------------------------------------------------------------
// Helper: fetch autenticado del servidor
// ---------------------------------------------------------------------------

/**
 * Realiza una petición GET autenticada al gateway desde el servidor.
 * Lanza un objeto estructurado { code, detail, status } en caso de error.
 *
 * @param {string} path   — ruta relativa, ej. '/expedientes/{id}'
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
      code: body.error ?? "upstream_error",
      detail: body.detail ?? `HTTP ${res.status}`,
      status: res.status,
    };
  }

  return res.json();
}

// ---------------------------------------------------------------------------
// load — composición SSR paralela
// ---------------------------------------------------------------------------

export async function load({ params, cookies, parent }) {
  const { id } = params;

  // Sesión ya validada por (app)/+layout.server.js
  const { user } = await parent();
  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";
  const userId = user?.sub ?? "";

  if (!id) {
    error(400, { message: "ID de expediente requerido" });
  }

  // -------------------------------------------------------------------------
  // Facetas en paralelo — allSettled: el fallo de una no tumba las demás
  // -------------------------------------------------------------------------
  const [resumenResult, indiceResult, eventosResult, ubicacionResult] = await Promise.allSettled([
    // Faceta 1: Resumen (expediente + radicados[])
    gatewayGet(`/expedientes/${id}`, token, tenant, userId ? { "X-User-Id": userId } : {}),

    // Faceta 2: Índice electrónico E15
    gatewayGet(`/expedientes/${id}/indice`, token, tenant),

    // Faceta 3: Eventos / hoja de ruta (E02 §9); X-User-Id acota por clearance
    gatewayGet(`/expedientes/${id}/eventos`, token, tenant, userId ? { "X-User-Id": userId } : {}),

    // Faceta 4: Ubicación física E17
    gatewayGet(`/expedientes/${id}/unidades`, token, tenant),
  ]);

  // Si el expediente principal falla → error de página (no puede mostrar nada útil)
  if (resumenResult.status === "rejected") {
    const err = resumenResult.reason;
    if (err?.status === 404) {
      error(404, { message: `Expediente ${id} no encontrado` });
    }
    if (err?.status === 403) {
      error(403, { message: "Sin permisos para ver este expediente" });
    }
    error(500, { message: err?.detail ?? "Error al cargar el expediente" });
  }

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
        detail: result.reason?.detail ?? "Error desconocido",
        status: result.reason?.status ?? 500,
      },
    };
  }

  return {
    expediente: resumenResult.value,
    facetas: {
      indice: settle(indiceResult),
      eventos: settle(eventosResult),
      ubicacion: settle(ubicacionResult),
    },
    // token, tenant y userId NO se exponen: las acciones cliente usan /expedientes/[id]/api/* (D-05)
  };
}
