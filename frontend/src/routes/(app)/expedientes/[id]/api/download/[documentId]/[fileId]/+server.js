// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /expedientes/[id]/api/download/[documentId]/[fileId] — Descarga de anexo
 * mediada por document-service (D-05, D-02, RF-SEG-08).
 *
 * CONTRATO NUEVO (cierre de fuga no-read-up en la descarga directa a
 * storage-service): la descarga ya NO es una URL pre-firmada sin clearance
 * (`GET /api/v1/storage/files/{fileId}/download`, retirada del gateway — 404).
 * Ahora document-service MEDIA el streaming del contenido:
 *
 *   GET /api/v1/documents/{document_id}/anexos/{file_id}/download
 *
 * `document_id` es el `radicado_id` DUEÑO del anexo (no el id del expediente
 * desde el que se navega — `[id]` de esta ruta solo da contexto de breadcrumb,
 * el backend no lo usa). El endpoint aplica no-read-up (clearance del llamante
 * >= nivel_seguridad del radicado) y devuelve 404 NEUTRO ante cualquier
 * denegación (radicado inexistente, sobre-clearance, o file_id que no es
 * anexo de ese radicado) — este proxy preserva esa opacidad, no la revela.
 *
 * El token se lee de event.locals (cookie httpOnly); el JS del cliente nunca
 * lo ve. X-User-Id se inyecta desde locals.user.sub (nunca del cliente) para
 * que document-service resuelva el clearance real del actor — sin él,
 * resolve_caller_clearance degrada a PUBLICA (fail-closed) y anexos de
 * radicados reservados/clasificados dejarían de descargar para usuarios que
 * sí tienen acceso.
 *
 * GET → retransmite el body binario en streaming (Content-Type/Content-Disposition
 *   del anexo, tal como los envía document-service) sin cargarlo completo en memoria.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  // Allowlist: documentId (radicado dueño del anexo) y fileId deben ser UUID.
  // Cierra la puerta a valores arbitrarios inyectados en la ruta antes de
  // reenviarlos al gateway.
  const { documentId, fileId } = params;
  if (!documentId || !UUID_RE.test(documentId)) {
    return json(
      { error: "validation_error", detail: "El identificador del radicado no es válido." },
      { status: 400 },
    );
  }
  if (!fileId || !UUID_RE.test(fileId)) {
    return json(
      { error: "validation_error", detail: "El identificador del anexo no es válido." },
      { status: 400 },
    );
  }

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  let res;
  try {
    res = await fetch(`${API_V1}/documents/${documentId}/anexos/${fileId}/download`, {
      headers,
    });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo descargar el anexo." },
      { status: 503 },
    );
  }

  if (!res.ok) {
    // No-read-up: el backend responde el MISMO 404 neutro para "no existe",
    // "sobre-clearance" y "file_id ajeno al radicado" — este proxy no debe
    // reconstruir ni filtrar esa distinción a partir de otras señales.
    if (res.status === 404) {
      return json({ error: "not_found", detail: "El anexo no está disponible." }, { status: 404 });
    }
    const body = await res.json().catch(() => ({
      error: "upstream_error",
      detail: `HTTP ${res.status}`,
    }));
    return json(body, { status: res.status });
  }

  // Retransmisión en streaming: no se materializa el archivo completo en
  // memoria del proceso SvelteKit, solo se reenvía el ReadableStream.
  const outHeaders = new Headers();
  const contentType = res.headers.get("content-type");
  const contentDisposition = res.headers.get("content-disposition");
  const contentLength = res.headers.get("content-length");
  outHeaders.set("Content-Type", contentType ?? "application/octet-stream");
  if (contentDisposition) outHeaders.set("Content-Disposition", contentDisposition);
  if (contentLength) outHeaders.set("Content-Length", contentLength);

  return new Response(res.body, { status: 200, headers: outHeaders });
}
