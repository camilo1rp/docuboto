// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /bandeja/api/[id]/anexos/[fileId] — descarga del anexo citado (D-05, D-02).
 *
 * `id` es el radicado dueño. document-service aplica no-read-up y responde
 * el mismo 404 si el radicado no existe, el clearance no alcanza o el archivo
 * no es de ese radicado. Este proxy no distingue esas causas.
 *
 *   GET /api/v1/documents/{id}/anexos/{fileId}/download
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const { id, fileId } = params;
  if (!id || !UUID.test(id) || !fileId || !UUID.test(fileId)) {
    return json(
      { error: "validation_error", detail: "El identificador del anexo no es válido." },
      { status: 400 },
    );
  }

  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  let res;
  try {
    res = await fetch(`${API_V1}/documents/${id}/anexos/${fileId}/download`, { headers });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo descargar el anexo." },
      { status: 503 },
    );
  }

  if (!res.ok) {
    if (res.status === 404) {
      return json({ error: "not_found", detail: "El anexo no está disponible." }, { status: 404 });
    }
    const body = await res.json().catch(() => ({
      error: "upstream_error",
      detail: `HTTP ${res.status}`,
    }));
    return json(body, { status: res.status });
  }

  const outHeaders = new Headers();
  const contentType = res.headers.get("content-type");
  const disposition = res.headers.get("content-disposition");
  const length = res.headers.get("content-length");
  outHeaders.set("Content-Type", contentType ?? "application/octet-stream");
  if (disposition) outHeaders.set("Content-Disposition", disposition);
  if (length) outHeaders.set("Content-Length", length);
  return new Response(res.body, { status: 200, headers: outHeaders });
}
