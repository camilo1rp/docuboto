// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /desclasificaciones/api/[id]/rechazar — Rechazar la desclasificación (C-2,
 * ADR-039, D-05). `[id]` es el de la SOLICITUD.
 *
 * POST → POST /api/v1/desclasificaciones/{id}/rechazar
 *   Body: { resolucion_motivo: string (10..500) }
 *   El motivo es obligatorio: quien solicitó necesita saber por qué (traza).
 *   Un motivo fuera de rango se RECHAZA (422), no se recorta en silencio.
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

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  const { resolucion_motivo } = body ?? {};
  const motivo = typeof resolucion_motivo === "string" ? resolucion_motivo.trim() : "";
  if (motivo.length < 10 || motivo.length > 500) {
    return json(
      {
        error: "validation_error",
        detail: "El motivo del rechazo debe tener entre 10 y 500 caracteres.",
      },
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
      `${API_V1}/desclasificaciones/${encodeURIComponent(params.id)}/rechazar`,
      {
        method: "POST",
        headers,
        body: JSON.stringify({ resolucion_motivo: motivo }),
      },
    );
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo rechazar la desclasificación." },
      { status: 503 },
    );
  }
}
