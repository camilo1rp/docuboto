// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /firmas/api/documento/[documentId]/verify — Proxy SSR para verificar un
 * hash de contenido contra las firmas de un radicado (E06, D-05).
 *
 * GET ?content_hash=<sha256 hex>
 *   → GET /api/v1/documents/{document_id}/signatures/verify?content_hash=
 *   → SignatureVerifyResponse { content_hash, valid, signatures[] }
 *
 * Gateado igual que el listado (PERM_FIRMA + no-read-up): un 404 significa
 * "no visible", nunca se reinterpreta como "hash no encontrado".
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SHA256_RE = /^[0-9a-f]{64}$/i;

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, params, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  const { documentId } = params;
  if (!documentId || !UUID_RE.test(documentId)) {
    return json(
      { error: "validation_error", detail: "El identificador del documento no es válido." },
      { status: 400 },
    );
  }

  const contentHash = url.searchParams.get("content_hash") ?? "";
  if (!SHA256_RE.test(contentHash)) {
    return json(
      { error: "validation_error", detail: "content_hash debe ser SHA-256 hex (64 caracteres)." },
      { status: 400 },
    );
  }

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  const params2 = new URLSearchParams({ content_hash: contentHash.toLowerCase() });

  try {
    const res = await fetch(
      `${API_V1}/documents/${encodeURIComponent(documentId)}/signatures/verify?${params2.toString()}`,
      { headers },
    );

    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de firmas." },
      { status: 503 },
    );
  }
}
