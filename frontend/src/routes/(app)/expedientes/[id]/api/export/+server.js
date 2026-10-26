// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /expedientes/[id]/api/export — Proxy SSR: exporta el expediente como ZIP
 * (índice XML + manifiesto + anexos) (D-05, ADR-011, Fase 2, E02).
 *
 * GET /api/v1/expedientes/{id}/export.zip → `application/zip` (archive-service
 * orquesta, storage-service ensambla). No-read-up: 404 si el expediente
 * supera el clearance del llamante.
 *
 * CUIDADO — descarga binaria potencialmente grande: se retransmite
 * `res.body` (ReadableStream) SIN materializar el ZIP completo en memoria del
 * proceso SvelteKit, mismo patrón que
 * `expedientes/[id]/api/download/[documentId]/[fileId]/+server.js`.
 *
 * El nombre de archivo lo decide el backend (`Content-Disposition` con el
 * `code` del expediente) — se sanea aquí por si acaso antes de reenviarlo
 * (nunca se interpola sin escapar en un header de respuesta propio).
 *
 * GET → application/zip (streaming, pass-through).
 */

import { API_V1 } from "$lib/utils/config.server.js";
import { json } from "@sveltejs/kit";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
// Solo caracteres seguros para un nombre de archivo de descarga; cualquier
// otra cosa (comillas, CR/LF, rutas) se descarta antes de componer el header.
const SAFE_FILENAME_RE = /^[\w.-]+$/;

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  const { id } = params;
  if (!id || !UUID_RE.test(id)) {
    return json(
      { error: "validation_error", detail: "Identificador de expediente inválido." },
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
    res = await fetch(`${API_V1}/expedientes/${encodeURIComponent(id)}/export.zip`, { headers });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo exportar el expediente." },
      { status: 503 },
    );
  }

  if (!res.ok) {
    if (res.status === 404) {
      return json(
        { error: "not_found", detail: "El expediente no está disponible para exportar." },
        { status: 404 },
      );
    }
    const body = await res.json().catch(() => ({
      error: "upstream_error",
      detail: `HTTP ${res.status}`,
    }));
    return json(body, { status: res.status });
  }

  // Extrae y sanea el filename del Content-Disposition upstream; si no viene
  // o trae caracteres fuera de la allowlist, usa un nombre genérico en vez de
  // reenviar un header potencialmente hostil.
  const upstreamDisposition = res.headers.get("content-disposition") ?? "";
  const match = /filename="?([^"]+)"?/.exec(upstreamDisposition);
  const rawFilename = match?.[1] ?? `expediente_${id}.zip`;
  const filename = SAFE_FILENAME_RE.test(rawFilename) ? rawFilename : `expediente_${id}.zip`;

  const outHeaders = new Headers();
  outHeaders.set("Content-Type", "application/zip");
  outHeaders.set("Content-Disposition", `attachment; filename="${filename}"`);
  const contentLength = res.headers.get("content-length");
  if (contentLength) outHeaders.set("Content-Length", contentLength);

  // Retransmisión en streaming: no se materializa el ZIP completo en memoria.
  return new Response(res.body, { status: 200, headers: outHeaders });
}
