// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Proxy SSR de `POST /api/v1/eliminacion/lotes/{id}/avalar` (RF-TRD-07, fase 3).
 *
 * El aval del Comité. Las tres constancias —acta, fecha y acto
 * administrativo— se exigen aquí y en el backend y en un CHECK de la base: un
 * aval sin ellas es una afirmación no verificada.
 *
 * **El 409 se reenvía con su cuerpo**, y no colapsado a «no se pudo avalar»:
 * el backend distingue «el plazo de publicidad sigue abierto hasta el X» de
 * «el lote no está publicado», y esa diferencia es justo lo que la persona
 * necesita para saber si tiene que esperar o corregir.
 */
import { json } from "@sveltejs/kit";

import { API_V1 } from "$lib/utils/config.server.js";
import { headersFor } from "$lib/server/trdCircuitoProxy.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

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
  const { acta_comite, fecha_aval, acto_administrativo } = body ?? {};

  if (typeof acta_comite !== "string" || acta_comite.trim().length < 3) {
    return json(
      { error: "validation_error", detail: "Indique el acta del Comité." },
      { status: 400 },
    );
  }
  if (typeof fecha_aval !== "string" || !DATE_RE.test(fecha_aval)) {
    return json(
      { error: "validation_error", detail: "Indique la fecha del aval (AAAA-MM-DD)." },
      { status: 400 },
    );
  }
  if (typeof acto_administrativo !== "string" || acto_administrativo.trim().length < 3) {
    return json(
      {
        error: "validation_error",
        detail: "Indique el acto administrativo que autoriza la eliminación.",
      },
      { status: 400 },
    );
  }

  const res = await fetch(
    `${API_V1}/eliminacion/lotes/${encodeURIComponent(params.loteId)}/avalar`,
    {
      method: "POST",
      headers: headersFor(locals, true),
      body: JSON.stringify({
        acta_comite: acta_comite.trim(),
        fecha_aval,
        acto_administrativo: acto_administrativo.trim(),
      }),
    },
  );
  const data = await res.json().catch(() => ({}));
  return json(data, { status: res.status });
}
