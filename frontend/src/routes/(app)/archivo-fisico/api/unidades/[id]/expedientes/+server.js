// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /archivo-fisico/api/unidades/[id]/expedientes — Proxy SSR vinculación expediente-unidad (D-05, E17).
 *
 * GET  → reenvía a GET /unidades/{id}/expedientes
 * POST → valida (expediente_id req), reenvía a POST /unidades/{id}/expedientes (204 → {ok:true})
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function GET({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const { id } = params;

  try {
    const res = await fetch(`${API_V1}/unidades/${id}/expedientes`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo obtener los expedientes de la unidad." },
      { status: 503 },
    );
  }
}

export async function POST({ locals, params, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const { id } = params;

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  const { expediente_id, folio_inicio, folio_fin } = body ?? {};

  if (!expediente_id || typeof expediente_id !== "string" || !UUID_RE.test(expediente_id.trim())) {
    return json(
      {
        error: "validation_error",
        detail: "El campo 'expediente_id' es obligatorio y debe ser un UUID válido.",
      },
      { status: 422 },
    );
  }

  /** @type {Record<string, unknown>} */
  const payload = { expediente_id: expediente_id.trim() };
  if (
    folio_inicio !== undefined &&
    folio_inicio !== null &&
    Number.isFinite(Number(folio_inicio))
  ) {
    payload.folio_inicio = Number(folio_inicio);
  }
  if (folio_fin !== undefined && folio_fin !== null && Number.isFinite(Number(folio_fin))) {
    payload.folio_fin = Number(folio_fin);
  }

  try {
    const res = await fetch(`${API_V1}/unidades/${id}/expedientes`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
      body: JSON.stringify(payload),
    });
    if (res.status === 204) {
      return json({ ok: true }, { status: 200 });
    }
    const data = await res.json().catch(() => ({ ok: true }));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo vincular el expediente a la unidad." },
      { status: 503 },
    );
  }
}
