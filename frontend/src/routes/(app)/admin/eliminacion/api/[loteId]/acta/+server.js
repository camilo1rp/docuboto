// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Proxy SSR de `POST /api/v1/eliminacion/lotes/{id}/acta` (RF-TRD-07, fase 4).
 *
 * Genera el acta de eliminación: **conservación permanente**, una sola vez y
 * antes del borrado.
 *
 * `metodo_borrado` es obligatorio y no lleva valor por defecto **tampoco
 * aquí**: un default en el proxy sería la misma frase inventada que el backend
 * se niega a poner, solo que escrita un piso más arriba.
 */
import { json } from "@sveltejs/kit";

import { API_V1 } from "$lib/utils/config.server.js";
import { headersFor } from "$lib/server/trdCircuitoProxy.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function POST({ params, request, locals, fetch }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  if (!UUID_RE.test(params.loteId ?? "")) {
    return json(
      { error: "validation_error", detail: "Identificador de lote inválido." },
      { status: 400 },
    );
  }
  const body = await request.json().catch(() => ({}));
  const metodo = typeof body?.metodo_borrado === "string" ? body.metodo_borrado.trim() : "";
  if (metodo.length < 10) {
    return json(
      {
        error: "validation_error",
        detail: "Declare cómo se destruyen los documentos (al menos 10 caracteres).",
      },
      { status: 400 },
    );
  }

  /** @type {Record<string, unknown>} */
  const payload = { metodo_borrado: metodo };
  if (typeof body?.observaciones === "string" && body.observaciones.trim()) {
    payload.observaciones = body.observaciones.trim();
  }

  const res = await fetch(`${API_V1}/eliminacion/lotes/${encodeURIComponent(params.loteId)}/acta`, {
    method: "POST",
    headers: headersFor(locals, true),
    body: JSON.stringify(payload),
  });
  const data = await res.json().catch(() => ({}));
  return json(data, { status: res.status });
}
