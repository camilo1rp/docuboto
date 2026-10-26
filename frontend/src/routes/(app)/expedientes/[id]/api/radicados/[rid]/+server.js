// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /expedientes/[id]/api/radicados/[rid] — Proxy SSR: excluir (soft-delete)
 * un radicado del expediente (D-05, ADR-011, Fase 2).
 *
 * DELETE /api/v1/expedientes/{id}/radicados/{radicado_id}?causal=... —
 * exige `USUA_PERM_EXPEDIENTE` con `min_crud=3` en el backend (archive-service,
 * `routers/expedientes.py::unlink_radicado`). E15 perfil v3 (RT-15 art.
 * 4.3.2.3): la exclusión es SOFT-DELETE — el vínculo permanece marcado
 * "excluido" en el índice (hueco trazable), no se borra físicamente.
 *
 * `causal` es OPCIONAL en el schema del backend (`max_length=500`), pero este
 * proxy lo exige: excluir un radicado de un expediente es un acto con
 * consecuencias archivísticas y debe quedar motivado en la hoja de ruta
 * (mismo criterio que `anulaciones/api/[id]/aprobar`, motivo obligatorio).
 *
 * POST → excluye el radicado del expediente (204 del backend → 200 { ok: true }).
 *   Body: { causal: string (1..500) }
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

  const { id, rid } = params;
  if (!id || !UUID_RE.test(id)) {
    return json(
      { error: "validation_error", detail: "Identificador de expediente inválido." },
      { status: 400 },
    );
  }
  if (!rid || !UUID_RE.test(rid)) {
    return json(
      { error: "validation_error", detail: "Identificador de radicado inválido." },
      { status: 400 },
    );
  }

  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  const { causal } = body ?? {};
  if (!causal || typeof causal !== "string" || !causal.trim()) {
    return json(
      { error: "validation_error", detail: "El motivo de la exclusión es obligatorio." },
      { status: 422 },
    );
  }
  if (causal.trim().length > 500) {
    return json(
      { error: "validation_error", detail: "El motivo no puede superar 500 caracteres." },
      { status: 422 },
    );
  }

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  const qs = new URLSearchParams({ causal: causal.trim() });

  try {
    const res = await fetch(
      `${API_V1}/expedientes/${encodeURIComponent(id)}/radicados/${encodeURIComponent(rid)}?${qs.toString()}`,
      { method: "DELETE", headers },
    );

    if (res.status === 204) {
      return json({ ok: true }, { status: 200 });
    }

    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo excluir el radicado." },
      { status: 503 },
    );
  }
}
