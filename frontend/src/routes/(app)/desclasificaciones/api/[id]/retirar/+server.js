// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /desclasificaciones/api/[id]/retirar — Retirar la propia solicitud de
 * desclasificación (C-2, ADR-039, D-05). `[id]` es el de la SOLICITUD.
 *
 * POST → POST /api/v1/desclasificaciones/{id}/retirar
 *   Body: { motivo?: string (<=500) } (opcional)
 *   El backend exige PERM_RECLASIFICAR crud>=3 y que quien retira sea quien
 *   solicitó (409 `solo_el_solicitante`). Retirar no cambia el acceso a nada.
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
  if (!params.id || !UUID_RE.test(params.id)) {
    return json(
      { error: "validation_error", detail: "Identificador de solicitud inválido." },
      { status: 400 },
    );
  }

  /** @type {any} */
  let body = null;
  try {
    body = await request.json();
  } catch {
    body = null;
  }
  const motivo = typeof body?.motivo === "string" ? body.motivo.trim() : "";
  if (motivo.length > 500) {
    return json(
      { error: "validation_error", detail: "El motivo no puede superar los 500 caracteres." },
      { status: 422 },
    );
  }

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": locals.user?.tenant_slug ?? "",
  };
  if (locals.user?.sub) headers["X-User-Id"] = locals.user.sub;

  try {
    const res = await fetch(
      `${API_V1}/desclasificaciones/${encodeURIComponent(params.id)}/retirar`,
      {
        method: "POST",
        headers,
        body: JSON.stringify(motivo ? { motivo } : {}),
      },
    );
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo retirar la solicitud." },
      { status: 503 },
    );
  }
}
