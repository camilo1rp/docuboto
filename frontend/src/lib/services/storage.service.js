// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * storage.service.js — Cliente del servicio de almacenamiento de archivos (E07).
 *
 * Endpoints reales del backend (vía gateway):
 *   POST /api/v1/storage/upload
 *     — multipart/form-data con campo `file` (UploadFile)
 *     — headers: Authorization Bearer, X-Tenant-Slug, X-User-Id
 *     — respuesta 201: FileResponse (id, filename, sha256, mime_type, file_size...)
 *     — el SHA-256 lo calcula el servidor; no se envía desde el cliente
 *
 *   GET  /api/v1/storage/files/{id}          — metadatos del archivo
 *   GET  /api/v1/storage/files/{id}/download  — URL pre-firmada de descarga
 *   DELETE /api/v1/storage/files/{id}         — eliminar archivo
 *
 * Todas las llamadas pasan por el api-gateway con token + X-Tenant-Slug (ADR-011).
 */

import { apiFetch } from "$lib/services/api.service.js";
import { API_V1 } from "$lib/utils/config.js";

// ---------------------------------------------------------------------------
// Subida de archivos
// ---------------------------------------------------------------------------

/**
 * Sube un archivo al storage-service vía el gateway.
 * El checksum SHA-256 lo calcula el servidor (RF-DIG-02); el cliente no lo envía.
 *
 * @param {File} file  objeto File del input o drop
 * @param {{ token?: string, tenant?: string, userId?: string }} [opts]
 * @returns {Promise<FileResponse>}
 */
export async function uploadFile(file, { token = "", tenant = "", userId } = {}) {
  const formData = new FormData();
  formData.append("file", file);

  const url = `${API_V1}/storage/upload`;

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  const res = await fetch(url, {
    method: "POST",
    headers,
    body: formData,
  });

  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw {
      error: body.error ?? "upload_error",
      detail: body.detail ?? `HTTP ${res.status}`,
      status: res.status,
    };
  }

  return res.json();
}

// ---------------------------------------------------------------------------
// Metadatos y descarga
// ---------------------------------------------------------------------------

/**
 * Obtiene los metadatos de un archivo almacenado.
 *
 * @param {string} fileId
 * @param {{ token?: string, tenant?: string }} [opts]
 * @returns {Promise<any>}
 */
export async function fetchFileMetadata(fileId, { token = "", tenant = "" } = {}) {
  return apiFetch(`/storage/files/${fileId}`, { token, tenant });
}

/**
 * Obtiene la URL pre-firmada de descarga de un archivo.
 *
 * @param {string} fileId
 * @param {{ token?: string, tenant?: string }} [opts]
 * @returns {Promise<any>}
 */
export async function getDownloadUrl(fileId, { token = "", tenant = "" } = {}) {
  return apiFetch(`/storage/files/${fileId}/download`, { token, tenant });
}

/**
 * Elimina un archivo del storage.
 *
 * @param {string} fileId
 * @param {{ token?: string, tenant?: string }} [opts]
 * @returns {Promise<void>}
 */
export async function deleteFile(fileId, { token = "", tenant = "" } = {}) {
  return apiFetch(`/storage/files/${fileId}`, {
    method: "DELETE",
    token,
    tenant,
  });
}

// ---------------------------------------------------------------------------
// JSDoc — Tipos alineados con FileResponse del backend
// ---------------------------------------------------------------------------

/**
 * @typedef {object} FileResponse
 * @property {string}       id           — UUID del archivo
 * @property {string}       filename
 * @property {string}       sha256       — checksum calculado por el servidor
 * @property {string|null}  mime_type
 * @property {number|null}  file_size    — bytes
 * @property {string}       uploaded_at  — ISO 8601
 * @property {string|null}  uploaded_by  — UUID del usuario
 * @property {string}       object_key   — clave en MinIO
 * @property {boolean}      is_current   — false si fue reemplazado
 */
