// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * archive.service.js — Cliente del servicio de expedientes y archivo físico (E02/E15/E17).
 *
 * Endpoints reales del backend (via gateway → archive-service):
 *
 * EXPEDIENTES
 *   GET  /api/v1/expedientes              — listado paginado (?status, page, size)
 *   POST /api/v1/expedientes              — crear expediente
 *   GET  /api/v1/expedientes/{id}         — detalle (incluye radicados[])
 *   PATCH /api/v1/expedientes/{id}        — actualizar campos
 *   POST /api/v1/expedientes/{id}/close   — cerrar (open → closed); requiere X-User-Id
 *   POST /api/v1/expedientes/{id}/transfer — transferir (closed → transferred)
 *   GET  /api/v1/expedientes/{id}/eventos  — hoja de ruta append-only
 *   GET  /api/v1/expedientes/{id}/foliado  — foliación ordenada (ExpedienteIndice)
 *
 * ÍNDICE ELECTRÓNICO (E15)
 *   GET  /api/v1/expedientes/{id}/indice          — índice vigente (IndiceResponse)
 *   GET  /api/v1/expedientes/{id}/indice.xml       — exportar XML (application/xml)
 *   GET  /api/v1/expedientes/{id}/indice/versions  — historial de versiones
 *   GET  /api/v1/expedientes/{id}/indice/verify    — verificar integridad
 *   POST /api/v1/expedientes/{id}/indice/rebuild   — regenerar (admin)
 *
 * UBICACIÓN FÍSICA (E17)
 *   GET  /api/v1/expedientes/{id}/unidades — dónde está el expediente físicamente
 *
 * Todas las llamadas pasan por el api-gateway con token + X-Tenant-Slug (ADR-011).
 */

import { apiFetch, ApiError, apiDownload } from "$lib/services/api.service.js";
import { API_V1 } from "$lib/utils/config.js";

// ---------------------------------------------------------------------------
// Helpers internos
// ---------------------------------------------------------------------------

/** @param {{ token?: string, tenant?: string }} opts */
function _baseOpts(opts) {
  return { token: opts.token ?? "", tenant: opts.tenant ?? "" };
}

/**
 * Lanza un ApiError a partir de una respuesta HTTP no-ok.
 * @param {Response} res
 */
async function _throwApiError(res) {
  const body = await res.json().catch(() => ({
    error: "upstream_error",
    detail: `HTTP ${res.status}`,
    status: res.status,
  }));
  throw new ApiError(
    body.error ?? "upstream_error",
    body.detail ?? `HTTP ${res.status}`,
    body.status ?? res.status,
  );
}

/**
 * POST autenticado que no envía body JSON (para acciones de estado como close/transfer).
 * @param {string} url
 * @param {string} token
 * @param {string} tenant
 * @param {Record<string,string>} [extraHeaders]
 */
async function _postNoBody(url, token, tenant, extraHeaders = {}) {
  const res = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      "X-Tenant-Slug": tenant,
      ...extraHeaders,
    },
  });
  if (!res.ok) await _throwApiError(res);
  return res.json();
}

// ---------------------------------------------------------------------------
// Expedientes
// ---------------------------------------------------------------------------

/**
 * Obtiene el detalle completo de un expediente (incluye radicados[]).
 *
 * @param {string} id  — UUID del expediente
 * @param {{ token?: string, tenant?: string, userId?: string }} opts
 * @returns {Promise<import('./archive.service.js').ExpedienteResponse>}
 */
export async function getExpediente(id, opts = {}) {
  const { token = "", tenant = "", userId } = opts;
  const url = `${API_V1}/expedientes/${id}`;
  /** @type {Record<string,string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;
  const res = await fetch(url, { headers });
  if (!res.ok) await _throwApiError(res);
  return res.json();
}

/**
 * Lista expedientes con paginación.
 *
 * @param {{ status?: string, page?: number, size?: number, token?: string, tenant?: string }} opts
 * @returns {Promise<{ items: any[], total: number }>}
 */
export async function listExpedientes(opts = {}) {
  const { status, page = 1, size = 20, token = "", tenant = "" } = opts;
  const params = new URLSearchParams({ page: String(page), size: String(size) });
  if (status) params.set("status", status);
  const res = await fetch(`${API_V1}/expedientes?${params}`, {
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      "X-Tenant-Slug": tenant,
    },
  });
  const total = parseInt(res.headers.get("X-Total-Count") ?? "0", 10);
  if (!res.ok) await _throwApiError(res);
  const items = await res.json();
  return { items, total };
}

/**
 * Cierra un expediente (open → closed).
 * Requiere X-User-Id (propagado como header opcional en el backend).
 *
 * @param {string} id
 * @param {{ token?: string, tenant?: string, userId?: string }} opts
 */
export async function closeExpediente(id, opts = {}) {
  const { token = "", tenant = "", userId } = opts;
  const extra = userId ? { "X-User-Id": userId } : {};
  return _postNoBody(`${API_V1}/expedientes/${id}/close`, token, tenant, extra);
}

/**
 * Transfiere un expediente (closed → transferred).
 *
 * @param {string} id
 * @param {{ token?: string, tenant?: string }} opts
 */
export async function transferExpediente(id, opts = {}) {
  const { token = "", tenant = "" } = opts;
  return _postNoBody(`${API_V1}/expedientes/${id}/transfer`, token, tenant);
}

/**
 * Obtiene la hoja de ruta (eventos append-only) del expediente.
 * X-User-Id es opcional pero acota por clearance.
 *
 * @param {string} id
 * @param {{ token?: string, tenant?: string, userId?: string }} opts
 */
export async function getExpedienteEventos(id, opts = {}) {
  const { token = "", tenant = "", userId } = opts;
  /** @type {Record<string,string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;
  const res = await fetch(`${API_V1}/expedientes/${id}/eventos`, { headers });
  if (!res.ok) await _throwApiError(res);
  return res.json();
}

// ---------------------------------------------------------------------------
// Índice electrónico E15
// ---------------------------------------------------------------------------

/**
 * Obtiene el índice electrónico del expediente (vigente o por versión).
 *
 * @param {string} id
 * @param {{ version?: number, token?: string, tenant?: string }} opts
 * @returns {Promise<import('./archive.service.js').IndiceResponse>}
 */
export async function getIndice(id, opts = {}) {
  const { version, token = "", tenant = "" } = opts;
  const params = version != null ? `?version=${version}` : "";
  return apiFetch(`/expedientes/${id}/indice${params}`, { token, tenant });
}

/**
 * Descarga el XML del índice electrónico (devuelve Blob).
 *
 * @param {string} id
 * @param {{ version?: number, token?: string, tenant?: string }} opts
 * @returns {Promise<Blob>}
 */
export async function downloadIndiceXml(id, opts = {}) {
  const { version, token = "", tenant = "" } = opts;
  const params = version != null ? `?version=${version}` : "";
  return apiDownload(`/expedientes/${id}/indice.xml${params}`, { token, tenant });
}

/**
 * Lista el historial de versiones del índice.
 *
 * @param {string} id
 * @param {{ page?: number, size?: number, token?: string, tenant?: string }} opts
 */
export async function listIndiceVersions(id, opts = {}) {
  const { page = 1, size = 20, token = "", tenant = "" } = opts;
  return apiFetch(`/expedientes/${id}/indice/versions?page=${page}&size=${size}`, {
    token,
    tenant,
  });
}

/**
 * Verifica la integridad del índice electrónico.
 *
 * @param {string} id
 * @param {{ version?: number, token?: string, tenant?: string }} opts
 */
export async function verifyIndice(id, opts = {}) {
  const { version, token = "", tenant = "" } = opts;
  const params = version != null ? `?version=${version}` : "";
  return apiFetch(`/expedientes/${id}/indice/verify${params}`, { token, tenant });
}

// ---------------------------------------------------------------------------
// Ubicación física E17
// ---------------------------------------------------------------------------

/**
 * Obtiene las unidades de conservación física donde está el expediente.
 * Endpoint: GET /api/v1/expedientes/{id}/unidades
 *
 * @param {string} id
 * @param {{ token?: string, tenant?: string }} opts
 * @returns {Promise<import('./archive.service.js').UnidadDeExpediente[]>}
 */
export async function getUbicacionFisica(id, opts = {}) {
  return apiFetch(`/expedientes/${id}/unidades`, _baseOpts(opts));
}

// ---------------------------------------------------------------------------
// JSDoc — tipos de respuesta del backend (para IDE)
// ---------------------------------------------------------------------------

/**
 * @typedef {Object} ExpedienteResponse
 * @property {string} id
 * @property {string} code
 * @property {string} name
 * @property {string|null} description
 * @property {string|null} trd_serie_id
 * @property {'open'|'closed'|'transferred'} status
 * @property {string|null} opened_by
 * @property {string} opened_at
 * @property {string|null} closed_at
 * @property {string} updated_at
 * @property {Object} metadata
 * @property {Object|null} disposition
 * @property {ExpedienteRadicado[]} radicados
 */

/**
 * @typedef {Object} ExpedienteRadicado
 * @property {string} radicado_id
 * @property {string} tracking_number
 * @property {string} added_at
 */

/**
 * @typedef {Object} ExpedienteEvento
 * @property {string} id
 * @property {string} expediente_id
 * @property {string|null} numero_expediente
 * @property {string} accion
 * @property {string|null} actor
 * @property {string|null} comentario
 * @property {Object} detalles
 * @property {string} ts
 */

/**
 * @typedef {Object} IndiceResponse
 * @property {string} id
 * @property {string} expediente_id
 * @property {number} version
 * @property {string} estado
 * @property {number} num_documentos
 * @property {string} xml_sha256
 * @property {string} algoritmo
 * @property {string|null} firma_id
 * @property {string} generado_ts
 * @property {IndiceItem[]} items
 */

/**
 * @typedef {Object} IndiceItem
 * @property {string} document_id
 * @property {number} orden
 * @property {string|null} titulo
 * @property {string|null} tipologia
 * @property {string|null} fecha_archivado
 * @property {string|null} valor_huella
 * @property {string} funcion_resumen
 */

/**
 * @typedef {Object} UnidadDeExpediente
 * @property {string} unidad_id
 * @property {string|null} signatura
 * @property {string|null} ubicacion_id
 * @property {number|null} folio_inicio
 * @property {number|null} folio_fin
 */
