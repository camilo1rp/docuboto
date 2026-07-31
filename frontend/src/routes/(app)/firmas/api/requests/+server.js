// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /firmas/api/requests — Proxy SSR para crear una cadena de firma y para
 * consultarla por objeto (E06, ADR-016, D-05).
 *
 * POST { objeto_tipo, objeto_id, firmantes[], titulo?, payload_sha256?, nivel_seguridad? }
 *   → POST /api/v1/signature/requests
 *   → SolicitudResponse (201)
 *
 * GET ?objeto_tipo=&objeto_id=
 *   → GET /api/v1/signature/requests?objeto_tipo=&objeto_id=
 *   → SolicitudResponse de la cadena ABIERTA para ese objeto (404 si no hay).
 *
 * Allowlist estricta en el POST: solo los campos de SolicitudCreate — nunca
 * el body íntegro (anti mass-assignment). El firmante creador se toma del
 * JWT en el backend (RF-FIR-11); no viaja en el body.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const SHA256_RE = /^[0-9a-f]{64}$/;

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  const { objeto_tipo, objeto_id, firmantes, titulo, payload_sha256, nivel_seguridad } = body ?? {};

  if (!objeto_tipo || typeof objeto_tipo !== "string" || !objeto_tipo.trim()) {
    return json(
      { error: "validation_error", detail: "objeto_tipo es obligatorio." },
      { status: 422 },
    );
  }
  if (!objeto_id || typeof objeto_id !== "string" || !objeto_id.trim()) {
    return json(
      { error: "validation_error", detail: "objeto_id es obligatorio." },
      { status: 422 },
    );
  }
  if (
    !Array.isArray(firmantes) ||
    firmantes.length === 0 ||
    !firmantes.every((f) => typeof f === "string" && UUID_RE.test(f))
  ) {
    return json(
      {
        error: "validation_error",
        detail: "firmantes debe ser un array no vacío de UUID válidos.",
      },
      { status: 422 },
    );
  }
  if (payload_sha256 !== undefined && payload_sha256 !== null && !SHA256_RE.test(payload_sha256)) {
    return json(
      { error: "validation_error", detail: "payload_sha256 debe ser SHA-256 hex (64 caracteres)." },
      { status: 422 },
    );
  }
  if (
    nivel_seguridad !== undefined &&
    nivel_seguridad !== null &&
    ![1, 2, 3].includes(Number(nivel_seguridad))
  ) {
    return json(
      { error: "validation_error", detail: "nivel_seguridad debe ser 1, 2 o 3." },
      { status: 422 },
    );
  }

  /** @type {Record<string, unknown>} */
  const payload = { objeto_tipo, objeto_id, firmantes };
  if (titulo) payload.titulo = String(titulo);
  if (payload_sha256) payload.payload_sha256 = payload_sha256;
  if (nivel_seguridad !== undefined && nivel_seguridad !== null) {
    payload.nivel_seguridad = Number(nivel_seguridad);
  }

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/signature/requests`, {
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

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";

  const objetoTipo = url.searchParams.get("objeto_tipo") ?? "";
  const objetoId = url.searchParams.get("objeto_id") ?? "";
  if (!objetoTipo.trim() || !objetoId.trim()) {
    return json(
      { error: "validation_error", detail: "objeto_tipo y objeto_id son obligatorios." },
      { status: 400 },
    );
  }

  const params = new URLSearchParams({ objeto_tipo: objetoTipo, objeto_id: objetoId });

  try {
    const res = await fetch(`${API_V1}/signature/requests?${params.toString()}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
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
