// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Proxy SSR de `POST /api/v1/eliminacion/lotes/{id}/acta/firmar` (RF-TRD-07, fase 4).
 *
 * Reintento del sellado XAdES. **No regenera nada** y no lleva cuerpo: sella el
 * mismo XML ya congelado. No acepta ningún campo del cliente a propósito —
 * cualquier dato que viajara aquí sería un dato que podría cambiar lo que se
 * firma.
 */
import { json } from "@sveltejs/kit";

import { API_V1 } from "$lib/utils/config.server.js";
import { headersFor } from "$lib/server/trdCircuitoProxy.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function POST({ params, locals, fetch }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  if (!UUID_RE.test(params.loteId ?? "")) {
    return json(
      { error: "validation_error", detail: "Identificador de lote inválido." },
      { status: 400 },
    );
  }

  const res = await fetch(
    `${API_V1}/eliminacion/lotes/${encodeURIComponent(params.loteId)}/acta/firmar`,
    { method: "POST", headers: headersFor(locals, true) },
  );
  const data = await res.json().catch(() => ({}));
  return json(data, { status: res.status });
}
