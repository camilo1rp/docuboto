// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /firmas/api/documento/[documentId] — Proxy SSR de la firma a nivel
 * documento (radicado/anexo) en document-service (E06, D-05).
 *
 * Distinta del flujo de cadena de signature-service (`/firmas/api/[id]`):
 * aquí se firma el HASH SHA-256 del contenido de un radicado/anexo
 * directamente, sin turnos ni segundo factor — usada por integraciones o
 * pantallas de detalle de documento que ya calculan el hash del contenido.
 *
 * POST { content_hash, anexo_id?, reason? }
 *   → POST /api/v1/documents/{document_id}/signatures
 *   → SignatureResponse (201)
 *
 * GET → GET /api/v1/documents/{document_id}/signatures
 *   → SignatureResponse[] (X-Total-Count)
 *
 * No-read-up (RF-SEG-08): el backend devuelve 404 NEUTRO si el radicado no
 * existe o supera el clearance del actor — este proxy reenvía ese 404 tal
 * cual, sin reinterpretarlo como "sin firmas".
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SHA256_RE = /^[0-9a-f]{64}$/i;

function badDocumentId() {
  return json(
    { error: "validation_error", detail: "El identificador del documento no es válido." },
    { status: 400 },
  );
}

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  const { documentId } = params;
  if (!documentId || !UUID_RE.test(documentId)) return badDocumentId();

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/documents/${encodeURIComponent(documentId)}/signatures`, {
      headers,
    });

    const headerTotal = res.headers.get("X-Total-Count");
    const data = await res.json().catch(() => ({}));
    if (!res.ok) return json(data, { status: res.status });

    const signatures = Array.isArray(data) ? data : [];
    const total = headerTotal ? Number(headerTotal) : signatures.length;
    return json({ signatures, total });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de firmas." },
      { status: 503 },
    );
  }
}

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, params, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  const username = locals.user?.preferred_username ?? "";

  const { documentId } = params;
  if (!documentId || !UUID_RE.test(documentId)) return badDocumentId();

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  const { content_hash, anexo_id, reason } = body ?? {};
  if (!content_hash || typeof content_hash !== "string" || !SHA256_RE.test(content_hash)) {
    return json(
      { error: "validation_error", detail: "content_hash debe ser SHA-256 hex (64 caracteres)." },
      { status: 422 },
    );
  }
  if (anexo_id !== undefined && anexo_id !== null && !UUID_RE.test(anexo_id)) {
    return json({ error: "validation_error", detail: "anexo_id inválido." }, { status: 422 });
  }
  if (reason !== undefined && reason !== null && typeof reason !== "string") {
    return json({ error: "validation_error", detail: "reason inválido." }, { status: 422 });
  }

  /** @type {Record<string, unknown>} */
  const payload = { content_hash: content_hash.toLowerCase() };
  if (anexo_id) payload.anexo_id = anexo_id;
  if (reason) payload.reason = String(reason).slice(0, 500);

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;
  if (username) headers["X-Username"] = username;

  try {
    const res = await fetch(`${API_V1}/documents/${encodeURIComponent(documentId)}/signatures`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });

    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de firmas." },
      { status: 503 },
    );
  }
}
