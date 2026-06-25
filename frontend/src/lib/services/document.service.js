// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * document.service.js — Cliente del servicio de radicados (E01).
 *
 * Endpoints reales del backend (via gateway):
 *   GET  /api/v1/documents          — listado paginado con filtros
 *     query params: doc_type (E|S|I), status, page, size
 *     header respuesta: X-Total-Count
 *   GET  /api/v1/documents/{id}     — detalle por UUID
 *   PATCH /api/v1/documents/{id}    — actualizar campos mutables (status, subject, dest_dept, pages, observations)
 *   PATCH /api/v1/documents/{id}/disposition — actualizar metadatos de disposición (RF-MET-08)
 *
 * Endpoint de clasificación en expediente:
 *   GET  /api/v1/expedientes/{id}   — existe en archive-service (T-10)
 *
 * Filtros de fecha: el endpoint /documents acepta solo doc_type y status.
 * Para filtros avanzados (date_from, date_to, dest_dept_code, q) usar /search (T-11).
 *
 * Todas las llamadas pasan por el api-gateway con token + X-Tenant-Slug (ADR-011).
 */

import { apiFetch, apiPatch, apiPost } from "$lib/services/api.service.js";

// ---------------------------------------------------------------------------
// Creación de radicado (T-09, RF-UI-04)
// ---------------------------------------------------------------------------

/**
 * Crea un nuevo radicado (radicación E/S/I) — confirmación atómica del stepper.
 *
 * Payload alineado con DocumentCreate del backend:
 *   doc_type:          'E'|'S'|'I'
 *   subject:           string (requerido)
 *   doc_class:         string|null   — código del tipo documental (doc_class)
 *   origin_dept_code:  number|null   — código numérico de la dependencia origen
 *   dest_dept_code:    number|null   — código numérico de la dependencia destino
 *   sender_name:       string|null   — remitente (Entradas)
 *   sender_entity:     string|null   — entidad del remitente
 *   pages:             number        — número de folios (default 0)
 *   observations:      string|null
 *   nivel_seguridad:   1|2|3         — 1=PUBLICA (default)
 *   response_days:     number|null   — plazo en días hábiles (RF-RAD-04)
 *   metadata:          object        — campos extra del tipo documental (E03)
 *   anexos:            AnexoInput[]  — archivos ya subidos al storage
 *
 * Retorna DocumentResponse con tracking_number asignado por el backend.
 *
 * @param {DocumentCreate} payload
 * @param {{ token?: string, tenant?: string }} [opts]
 * @returns {Promise<DocumentResponse>}
 */
export async function createDocument(payload, { token = "", tenant = "" } = {}) {
  return apiPost("/documents", payload, { token, tenant });
}

// ---------------------------------------------------------------------------
// Listado paginado
// ---------------------------------------------------------------------------

/**
 * Carga la bandeja de radicados con filtros opcionales.
 *
 * @param {BandejaParams} params
 * @returns {Promise<{ documents: DocumentResponse[], total: number }>}
 */
export async function fetchDocuments({
  token,
  tenant,
  docType,
  status,
  page = 1,
  size = 20,
  fetchFn,
} = {}) {
  const params = new URLSearchParams({ page: String(page), size: String(size) });
  if (docType) params.set("doc_type", docType);
  if (status) params.set("status", status);

  // En SSR usamos el fetch inyectado por SvelteKit para que el gateway sea
  // alcanzable vía la red interna del contenedor; en cliente usamos apiFetch.
  const url = `/documents?${params.toString()}`;

  if (fetchFn) {
    // Camino SSR: fetchFn es el fetch de SvelteKit con credenciales del servidor
    const { API_V1 } = await import("$lib/utils/config.js");
    const res = await fetchFn(`${API_V1}${url}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant ?? "",
      },
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw {
        error: body.error ?? "fetch_error",
        detail: body.detail ?? `HTTP ${res.status}`,
        status: res.status,
      };
    }
    const total = Number(res.headers.get("X-Total-Count") ?? 0);
    const documents = await res.json();
    return { documents, total };
  }

  // Camino cliente: apiFetch — no puede leer X-Total-Count desde aquí porque
  // apiFetch retorna solo el JSON; hacemos fetch directo con buildHeaders lógico.
  const { API_V1 } = await import("$lib/utils/config.js");
  const res = await fetch(`${API_V1}${url}`, {
    headers: {
      "Content-Type": "application/json",
      Authorization: `Bearer ${token}`,
      "X-Tenant-Slug": tenant ?? "",
    },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw {
      error: body.error ?? "fetch_error",
      detail: body.detail ?? `HTTP ${res.status}`,
      status: res.status,
    };
  }
  const total = Number(res.headers.get("X-Total-Count") ?? 0);
  const documents = await res.json();
  return { documents, total };
}

// ---------------------------------------------------------------------------
// Detalle de radicado
// ---------------------------------------------------------------------------

/**
 * Obtiene un radicado por UUID.
 *
 * @param {string} id
 * @param {object} [opts]
 * @param {string} [opts.token]
 * @param {string} [opts.tenant]
 * @returns {Promise<DocumentResponse>}
 */
export async function fetchDocumentById(id, { token = "", tenant = "" } = {}) {
  return apiFetch(`/documents/${id}`, { token, tenant });
}

// ---------------------------------------------------------------------------
// Actualizar radicado (campos mutables)
// ---------------------------------------------------------------------------

/**
 * Actualiza campos mutables de un radicado.
 * Campos permitidos por el backend: subject, dest_dept, observations, status, pages.
 *
 * @param {string} id
 * @param {Partial<DocumentUpdate>} payload
 * @param {object} [opts]
 * @param {string} [opts.token]
 * @param {string} [opts.tenant]
 * @returns {Promise<DocumentResponse>}
 */
export async function updateDocument(id, payload, { token = "", tenant = "" } = {}) {
  return apiPatch(`/documents/${id}`, payload, { token, tenant });
}

// ---------------------------------------------------------------------------
// JSDoc — Tipos alineados con DocumentResponse del backend
// ---------------------------------------------------------------------------

/**
 * @typedef {object} BandejaParams
 * @property {string}  [token]
 * @property {string}  [tenant]
 * @property {string}  [docType]   - 'E'|'S'|'I'
 * @property {string}  [status]    - 'registered'|'distributed'|'in_progress'|'closed'
 * @property {number}  [page]
 * @property {number}  [size]
 * @property {Function} [fetchFn] - fetch de SvelteKit (SSR) o undefined (cliente)
 */

/**
 * @typedef {object} AnexoResponse
 * @property {string}  id
 * @property {string}  file_id
 * @property {string}  filename
 * @property {number|null} file_size
 * @property {string|null} mime_type
 * @property {string}  created_at
 * @property {string|null} tipo_anexo
 * @property {boolean} es_principal
 * @property {number|null} folios
 */

/**
 * @typedef {object} DocumentResponse
 * @property {string}  id
 * @property {string}  tracking_number     — ej. '2024-ICETEX-E-001234'
 * @property {string}  doc_type            — 'E'|'S'|'I'
 * @property {number}  year
 * @property {number}  sequence
 * @property {string}  subject
 * @property {string|null} doc_class
 * @property {string|null} origin_dept
 * @property {string|null} dest_dept
 * @property {number|null} origin_dept_code
 * @property {number|null} dest_dept_code
 * @property {string|null} sender_name
 * @property {string|null} sender_entity
 * @property {string}  status              — 'registered'|'distributed'|'in_progress'|'closed'
 * @property {number}  pages
 * @property {string|null} observations
 * @property {string|null} registered_by   — UUID
 * @property {string}  registered_at       — ISO 8601
 * @property {string}  updated_at          — ISO 8601
 * @property {string|null} due_date        — ISO date (RF-RAD-04)
 * @property {Record<string,any>} metadata
 * @property {AnexoResponse[]} anexos
 */

/**
 * @typedef {object} DocumentUpdate
 * @property {string|null} [subject]
 * @property {string|null} [dest_dept]
 * @property {string|null} [observations]
 * @property {'registered'|'distributed'|'in_progress'|'closed'|null} [status]
 * @property {number|null} [pages]
 */

/**
 * @typedef {object} AnexoInput
 * @property {string}  file_id       — UUID del archivo subido al storage
 * @property {string}  filename
 * @property {number|null} [file_size]
 * @property {string|null} [mime_type]
 * @property {string|null} [tipo_anexo]
 * @property {boolean} [es_principal]
 * @property {number|null} [folios]
 */

/**
 * @typedef {object} DocumentCreate
 * @property {'E'|'S'|'I'}  doc_type
 * @property {string}        subject
 * @property {string|null}   [doc_class]
 * @property {string|null}   [origin_dept]
 * @property {string|null}   [dest_dept]
 * @property {number|null}   [origin_dept_code]
 * @property {number|null}   [dest_dept_code]
 * @property {string|null}   [sender_name]
 * @property {string|null}   [sender_entity]
 * @property {number}        [pages]
 * @property {string|null}   [observations]
 * @property {1|2|3}         [nivel_seguridad]
 * @property {number|null}   [response_days]
 * @property {object}        [metadata]
 * @property {AnexoInput[]}  [anexos]
 */
