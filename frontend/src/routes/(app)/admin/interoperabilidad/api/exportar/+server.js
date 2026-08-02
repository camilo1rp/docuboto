// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin/interoperabilidad/api/exportar — Proxy SSR de exportación interoperable
 * de radicados (E11, RF-INT-01) (D-05, Fase 8).
 *
 * POST /api/v1/export — cuerpo JSON {comentario?, year?, doc_type?}.
 * Gate real: USUA_PERM_EXPEDIENTE (backend). Respuesta `application/zip`.
 *
 * CUIDADO — descarga binaria: se retransmite `res.body` (ReadableStream) SIN
 * materializar el ZIP completo en memoria (mismo patrón que
 * expedientes/[id]/api/export/+server.js). El nombre de archivo lo decide el
 * backend vía Content-Disposition; se sanea aquí antes de reenviarlo. También
 * se reenvían los contadores X-Export-Count/X-Export-Excluded como headers
 * propios (`X-OrpycaMCP-*`) para que el cliente los lea sin parsear el ZIP.
 */

import { API_V1 } from "$lib/utils/config.server.js";
import { json } from "@sveltejs/kit";

const SAFE_FILENAME_RE = /^[\w.\-]+$/;

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  let body = {};
  try {
    body = await request.json();
  } catch {
    body = {};
  }

  // Allowlist campo a campo — nunca reenviar el body crudo del cliente.
  /** @type {Record<string, unknown>} */
  const payload = {};
  if (typeof body.comentario === "string" && body.comentario.trim()) {
    payload.comentario = body.comentario.trim().slice(0, 500);
  }
  if (Number.isInteger(body.year)) payload.year = body.year;
  if (typeof body.doc_type === "string" && /^[ESI]$/.test(body.doc_type)) {
    payload.doc_type = body.doc_type;
  }

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  let res;
  try {
    res = await fetch(`${API_V1}/export`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de exportación." },
      { status: 503 },
    );
  }

  if (!res.ok) {
    const errBody = await res.json().catch(() => ({
      error: "upstream_error",
      detail: `HTTP ${res.status}`,
    }));
    return json(errBody, { status: res.status });
  }

  const upstreamDisposition = res.headers.get("content-disposition") ?? "";
  const match = /filename="?([^"]+)"?/.exec(upstreamDisposition);
  const rawFilename = match?.[1] ?? "orpyca-export.zip";
  const filename = SAFE_FILENAME_RE.test(rawFilename) ? rawFilename : "orpyca-export.zip";

  const outHeaders = new Headers();
  outHeaders.set("Content-Type", "application/zip");
  outHeaders.set("Content-Disposition", `attachment; filename="${filename}"`);
  const contentLength = res.headers.get("content-length");
  if (contentLength) outHeaders.set("Content-Length", contentLength);
  // Contadores del manifiesto (no read-up): se exponen para que el cliente
  // muestre cuántos radicados se incluyeron/excluyeron sin parsear el ZIP.
  const exportUuid = res.headers.get("x-export-uuid");
  const exportCount = res.headers.get("x-export-count");
  const exportExcluded = res.headers.get("x-export-excluded");
  if (exportUuid) outHeaders.set("X-OrpycaMCP-Export-UUID", exportUuid);
  if (exportCount) outHeaders.set("X-OrpycaMCP-Export-Count", exportCount);
  if (exportExcluded) outHeaders.set("X-OrpycaMCP-Export-Excluded", exportExcluded);

  // Retransmisión en streaming: no se materializa el ZIP completo en memoria.
  return new Response(res.body, { status: 200, headers: outHeaders });
}
