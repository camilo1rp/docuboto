// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Proxy SSR de `POST /api/v1/eliminacion/lotes/{id}/anular` (RF-TRD-07, fase 3).
 *
 * Retira el lote del procedimiento. El motivo tiene mínimo real (10
 * caracteres, igual que el CHECK de la base) porque es la constancia de por
 * qué NO se eliminó — y si el lote se llegó a publicar, esa constancia se ve
 * en la pantalla pública.
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
  const motivo = typeof body?.motivo === "string" ? body.motivo.trim() : "";
  if (motivo.length < 10) {
    return json(
      {
        error: "validation_error",
        detail: "Explique por qué se retira el lote (al menos 10 caracteres).",
      },
      { status: 400 },
    );
  }

  const res = await fetch(
    `${API_V1}/eliminacion/lotes/${encodeURIComponent(params.loteId)}/anular`,
    {
      method: "POST",
      headers: headersFor(locals, true),
      body: JSON.stringify({ motivo }),
    },
  );
  const data = await res.json().catch(() => ({}));
  return json(data, { status: res.status });
}
