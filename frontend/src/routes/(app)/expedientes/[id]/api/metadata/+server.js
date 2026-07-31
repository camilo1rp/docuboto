// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /expedientes/[id]/api/metadata — Proxy SSR: editar metadatos del expediente
 * (D-05, ADR-011, Fase 2 cierre desfase API↔UI).
 *
 * PATCH /api/v1/expedientes/{id} — `ExpedienteUpdate` (archive-service,
 * `app/schemas/archive.py`), `extra="forbid"` en el backend: el ciclo de vida
 * (open→closed→transferred) transita EXCLUSIVAMENTE por
 * POST /{id}/close y /{id}/transfer — este PATCH nunca acepta `status`.
 *
 * Allowlist campo a campo (anti mass-assignment): `name`, `description`,
 * `trd_serie_id` — solo se incluyen en el payload los campos que el cliente
 * envió explícitamente (permite editar un subconjunto sin borrar los demás,
 * ya que el backend solo actualiza las claves presentes).
 *
 * POST → PATCH del expediente.
 *   Body: { name?: string, description?: string, trd_serie_id?: string|null }
 *   Respuesta: ExpedienteResponse o { error, detail, status }
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, params, request }) {
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

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  const { name, description, trd_serie_id: trdSerieId } = body ?? {};

  /** @type {Record<string, unknown>} */
  const payload = {};

  if (name !== undefined) {
    if (typeof name !== "string" || !name.trim()) {
      return json(
        { error: "validation_error", detail: "El nombre del expediente no puede quedar vacío." },
        { status: 422 },
      );
    }
    if (name.length > 500) {
      return json(
        { error: "validation_error", detail: "El nombre no puede superar 500 caracteres." },
        { status: 422 },
      );
    }
    payload.name = name.trim();
  }

  if (description !== undefined) {
    payload.description = typeof description === "string" ? description.trim() : null;
  }

  if (trdSerieId !== undefined) {
    if (trdSerieId === null || trdSerieId === "") {
      payload.trd_serie_id = null;
    } else if (typeof trdSerieId === "string" && UUID_RE.test(trdSerieId)) {
      payload.trd_serie_id = trdSerieId;
    } else {
      return json({ error: "validation_error", detail: "Serie TRD inválida." }, { status: 422 });
    }
  }

  if (Object.keys(payload).length === 0) {
    return json(
      { error: "validation_error", detail: "No hay cambios para guardar." },
      { status: 422 },
    );
  }

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };

  try {
    const res = await fetch(`${API_V1}/expedientes/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers,
      body: JSON.stringify(payload),
    });

    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudieron guardar los metadatos." },
      { status: 503 },
    );
  }
}
