// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * api.service.js — Cliente central del api-gateway OrpycaMCP.
 *
 * Reglas:
 *  - Toda llamada pasa por PUBLIC_API_URL (api-gateway). Nunca a un
 *    microservicio directamente (ADR-011).
 *  - Propaga `Authorization: Bearer <token>` y `X-Tenant-Slug` en cada
 *    petición (multi-tenancy transparente, ADR-002).
 *  - Maneja 401: llama al refreshFn proporcionado; si falla → redirige a /login.
 *  - Normaliza errores del backend `{error, detail, status}` en ApiError
 *    para que los componentes los muestren como toasts uniformes.
 *
 * Uso SSR (page.server.js) vs cliente:
 *   - En +page.server.js: pasar `token` y `tenant` explícitamente (los stores
 *     no están disponibles en el servidor).
 *   - En componentes cliente: los stores `auth` exportan helpers que inyectan
 *     token y tenant automáticamente.
 */

import { API_V1 } from "$lib/utils/config.js";

// ---------------------------------------------------------------------------
// Clase de error normalizado
// ---------------------------------------------------------------------------

/**
 * Error normalizado del backend OrpycaMCP.
 * shape: { error: string, detail: string, status: number }
 */
export class ApiError extends Error {
  /**
   * @param {string} error   — código de error (snake_case)
   * @param {string} detail  — mensaje legible para el usuario
   * @param {number} status  — código HTTP
   */
  constructor(error, detail, status) {
    super(detail);
    this.name = "ApiError";
    this.error = error;
    this.detail = detail;
    this.status = status;
  }

  /** Convierte el error en un objeto plano listo para el store de toasts */
  toToast() {
    return {
      type: this.status >= 500 ? "error" : this.status === 403 ? "warning" : "error",
      title: this._labelForStatus(this.status),
      message: this.detail,
      code: this.error,
    };
  }

  _labelForStatus(status) {
    if (status === 401) return "Sesión expirada";
    if (status === 403) return "Sin permisos";
    if (status === 404) return "No encontrado";
    if (status >= 500) return "Error del servidor";
    return "Error";
  }
}

// ---------------------------------------------------------------------------
// Función de parseo de error genérico
// ---------------------------------------------------------------------------

/**
 * Convierte cualquier respuesta HTTP no-ok en un ApiError normalizado.
 * @param {Response} res
 * @returns {Promise<ApiError>}
 */
async function parseError(res) {
  let body;
  try {
    body = await res.json();
  } catch {
    body = {
      error: "unexpected_error",
      detail: `HTTP ${res.status}`,
      status: res.status,
    };
  }
  return new ApiError(
    body.error ?? "unexpected_error",
    body.detail ?? `HTTP ${res.status}`,
    body.status ?? res.status,
  );
}

// ---------------------------------------------------------------------------
// Función principal: apiFetch
// ---------------------------------------------------------------------------

/**
 * Realiza una petición autenticada al api-gateway de OrpycaMCP.
 *
 * @param {string} path - Ruta relativa dentro de API_V1 (ej. '/documents') o URL completa.
 * @param {object} [options]
 * @param {string} [options.method] - Método HTTP (default: 'GET').
 * @param {object|null} [options.body] - Cuerpo JSON de la petición.
 * @param {FormData} [options.formData] - Para subir archivos (no serializa body).
 * @param {string} [options.token] - JWT Bearer.
 * @param {string} [options.tenant] - Slug del tenant (del claim JWT).
 * @param {Function} [options.refreshFn] - Async fn que renueva el token; devuelve nuevo token o null.
 * @param {Function} [options.onUnauth] - Callback cuando el 401 no se puede recuperar.
 * @param {RequestInit} [options.fetchOptions] - Opciones adicionales para fetch.
 * @returns {Promise<any>} - Datos parseados como JSON (o undefined si 204).
 * @throws {ApiError} - Error normalizado del backend.
 */
export async function apiFetch(path, options = {}) {
  const {
    method = "GET",
    body = undefined,
    formData = undefined,
    token,
    tenant,
    refreshFn,
    onUnauth,
    fetchOptions = {},
  } = options;

  // Construye URL: si ya es absoluta la respeta; si no, la prefija con API_V1
  const url = path.startsWith("http") ? path : `${API_V1}${path}`;

  const headers = buildHeaders({ token, tenant, hasFormData: !!formData });

  let res = await fetch(url, {
    method,
    headers,
    body: formData ?? (body !== undefined ? JSON.stringify(body) : undefined),
    ...fetchOptions,
  });

  // --- Manejo 401: intentar refresh una vez ----------------------------------
  if (res.status === 401 && refreshFn) {
    const newToken = await refreshFn().catch(() => null);
    if (newToken) {
      const retryHeaders = buildHeaders({
        token: newToken,
        tenant,
        hasFormData: !!formData,
      });
      res = await fetch(url, {
        method,
        headers: retryHeaders,
        body: formData ?? (body !== undefined ? JSON.stringify(body) : undefined),
        ...fetchOptions,
      });
    } else {
      // Refresh fallido: notificar y salir
      if (onUnauth) onUnauth();
      throw new ApiError(
        "session_expired",
        "La sesión ha expirado. Inicia sesión nuevamente.",
        401,
      );
    }
  }

  // --- Error persistente post-refresh ----------------------------------------
  if (!res.ok) {
    if (res.status === 401 && onUnauth) onUnauth();
    throw await parseError(res);
  }

  // 204 No Content
  if (res.status === 204) return undefined;

  return res.json();
}

// ---------------------------------------------------------------------------
// Helpers de headers
// ---------------------------------------------------------------------------

/**
 * Construye los headers estándar de OrpycaMCP.
 * @param {object} params
 * @param {string} [params.token]
 * @param {string} [params.tenant]
 * @param {boolean} [params.hasFormData]
 * @returns {Record<string, string>}
 */
function buildHeaders({ token, tenant, hasFormData = false }) {
  /** @type {Record<string, string>} */
  const headers = {};

  if (!hasFormData) {
    headers["Content-Type"] = "application/json";
  }
  if (token) {
    headers["Authorization"] = `Bearer ${token}`;
  }
  if (tenant) {
    headers["X-Tenant-Slug"] = tenant;
  }

  return headers;
}

// ---------------------------------------------------------------------------
// Conveniencia: métodos GET / POST / PUT / PATCH / DELETE
// ---------------------------------------------------------------------------

/**
 * GET autenticado.
 * @param {string} path
 * @param {Omit<Parameters<typeof apiFetch>[1], 'method'>} options
 */
export function apiGet(path, options = {}) {
  return apiFetch(path, { ...options, method: "GET" });
}

/**
 * POST autenticado con cuerpo JSON.
 * @param {string} path
 * @param {object} body
 * @param {Omit<Parameters<typeof apiFetch>[1], 'method'|'body'>} options
 */
export function apiPost(path, body, options = {}) {
  return apiFetch(path, { ...options, method: "POST", body });
}

/**
 * PUT autenticado con cuerpo JSON.
 */
export function apiPut(path, body, options = {}) {
  return apiFetch(path, { ...options, method: "PUT", body });
}

/**
 * PATCH autenticado con cuerpo JSON.
 */
export function apiPatch(path, body, options = {}) {
  return apiFetch(path, { ...options, method: "PATCH", body });
}

/**
 * DELETE autenticado.
 */
export function apiDelete(path, options = {}) {
  return apiFetch(path, { ...options, method: "DELETE" });
}

/**
 * Upload multipart/form-data autenticado (para anexos).
 * @param {string} path
 * @param {FormData} formData
 * @param {Omit<Parameters<typeof apiFetch>[1], 'method'|'formData'>} options
 */
export function apiUpload(path, formData, options = {}) {
  return apiFetch(path, { ...options, method: "POST", formData });
}

// ---------------------------------------------------------------------------
// Helper para descarga de CSV / binarios
// ---------------------------------------------------------------------------

/**
 * Descarga un archivo binario (CSV, PDF) del gateway.
 * Retorna el Blob directamente.
 * @param {string} path
 * @param {{ token?: string, tenant?: string }} [options]
 * @returns {Promise<Blob>}
 */
export async function apiDownload(path, options = {}) {
  const { token, tenant } = options;
  const url = path.startsWith("http") ? path : `${API_V1}${path}`;
  const res = await fetch(url, {
    headers: buildHeaders({ token, tenant }),
  });
  if (!res.ok) throw await parseError(res);
  return res.blob();
}
