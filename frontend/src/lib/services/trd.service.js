// OrpycaMCP -- Sistema de Gestion Documental Electronico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Publica General
// Affero de GNU (AGPL) version 3 o, a su eleccion, cualquier version posterior,
// conservando los terminos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTIA ALGUNA. Vease el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * trd.service.js -- Cliente de catalogos TRD y tipos documentales (E03/E04).
 *
 * Endpoints reales del backend (via gateway):
 *   GET /api/v1/trd                       series/subseries TRD paginadas
 *     query: is_active (bool), page, size
 *     header respuesta: X-Total-Count
 *   GET /api/v1/trd/{id}                  serie TRD por UUID
 *
 *   GET /api/v1/tipos-documentales        catalogo de tipos documentales
 *     query: trd_serie_id (UUID, opcional), activo (bool, opcional)
 *   GET /api/v1/tipos-documentales/{id}   tipo documental por UUID
 *
 *   GET /api/v1/metadata/templates        plantillas de metadatos
 *     query: tipo_documental (string, opcional)
 *
 * Todas las llamadas pasan por el api-gateway con token + X-Tenant-Slug (ADR-011).
 */

import { apiFetch } from "$lib/services/api.service.js";
import { API_V1 } from "$lib/utils/config.js";

// ---------------------------------------------------------------------------
// Series TRD
// ---------------------------------------------------------------------------

/**
 * Lista las series/subseries TRD activas del tenant.
 *
 * @param {{ token?: string, tenant?: string, isActive?: boolean, page?: number, size?: number, fetchFn?: Function }} [opts]
 * @returns {Promise<{ series: any[], total: number }>}
 */
export async function fetchTrdSeries({
  token = "",
  tenant = "",
  isActive = true,
  page = 1,
  size = 100,
  fetchFn,
} = {}) {
  const params = new URLSearchParams({ page: String(page), size: String(size) });
  if (isActive !== undefined && isActive !== null) params.set("is_active", String(isActive));

  const url = `${API_V1}/trd?${params.toString()}`;
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };

  const res = await (fetchFn ?? fetch)(url, { headers });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw {
      error: body.error ?? "fetch_error",
      detail: body.detail ?? `HTTP ${res.status}`,
      status: res.status,
    };
  }

  const total = Number(res.headers.get("X-Total-Count") ?? 0);
  const series = await res.json();
  return { series, total };
}

/**
 * Obtiene una serie TRD por UUID.
 *
 * @param {string} id
 * @param {{ token?: string, tenant?: string }} [opts]
 * @returns {Promise<any>}
 */
export async function fetchTrdSerieById(id, { token = "", tenant = "" } = {}) {
  return apiFetch(`/trd/${id}`, { token, tenant });
}

// ---------------------------------------------------------------------------
// Tipos documentales
// ---------------------------------------------------------------------------

/**
 * Lista los tipos documentales del tenant (catalogo E03).
 *
 * @param {{ token?: string, tenant?: string, trdSerieId?: string, activo?: boolean, fetchFn?: Function }} [opts]
 * @returns {Promise<any[]>}
 */
export async function fetchTiposDocumentales({
  token = "",
  tenant = "",
  trdSerieId,
  activo = true,
  fetchFn,
} = {}) {
  const params = new URLSearchParams();
  if (trdSerieId) params.set("trd_serie_id", trdSerieId);
  if (activo !== undefined && activo !== null) params.set("activo", String(activo));

  const url = `${API_V1}/tipos-documentales${params.toString() ? `?${params.toString()}` : ""}`;
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };

  const res = await (fetchFn ?? fetch)(url, { headers });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw {
      error: body.error ?? "fetch_error",
      detail: body.detail ?? `HTTP ${res.status}`,
      status: res.status,
    };
  }

  return res.json();
}

/**
 * Obtiene un tipo documental por UUID.
 *
 * @param {string} id
 * @param {{ token?: string, tenant?: string }} [opts]
 * @returns {Promise<any>}
 */
export async function fetchTipoDocumentalById(id, { token = "", tenant = "" } = {}) {
  return apiFetch(`/tipos-documentales/${id}`, { token, tenant });
}

// ---------------------------------------------------------------------------
// Plantillas de metadatos
// ---------------------------------------------------------------------------

/**
 * Lista las plantillas de metadatos, opcionalmente filtrando por tipo documental.
 * Las plantillas definen el JSON Schema de campos extra que se validan al radicar (E03).
 *
 * @param {{ token?: string, tenant?: string, tipoDocumental?: string, fetchFn?: Function }} [opts]
 * @returns {Promise<any[]>}
 */
export async function fetchMetadataTemplates({
  token = "",
  tenant = "",
  tipoDocumental,
  fetchFn,
} = {}) {
  const params = new URLSearchParams();
  if (tipoDocumental) params.set("tipo_documental", tipoDocumental);

  const url = `${API_V1}/metadata/templates${params.toString() ? `?${params.toString()}` : ""}`;
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };

  const res = await (fetchFn ?? fetch)(url, { headers });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw {
      error: body.error ?? "fetch_error",
      detail: body.detail ?? `HTTP ${res.status}`,
      status: res.status,
    };
  }

  return res.json();
}
