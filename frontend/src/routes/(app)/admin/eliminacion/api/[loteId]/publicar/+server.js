// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Proxy SSR de `POST /api/v1/eliminacion/lotes/{id}/publicar` (RF-TRD-07, fase 2).
 *
 * Vive bajo `/admin/eliminacion` y no bajo `/admin/trd` porque el lote **no es
 * una lectura del instrumento**: es un procedimiento con su propio ciclo de
 * vida y su propia pantalla, igual que en el backend es un router aparte. Lo
 * único que se queda en `/admin/trd` es PROPONER, porque se propone desde la
 * cola de vencimientos.
 *
 * Publicar **congela el inventario** y lo pone a la vista de cualquiera sin
 * autenticar. El plazo no tiene default ni aquí ni en el backend: no se inventa
 * cuántos días da una entidad para que un tercero se oponga a que destruyan un
 * documento.
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
  const { publicado_hasta } = body ?? {};
  if (typeof publicado_hasta !== "string" || !DATE_RE.test(publicado_hasta)) {
    return json(
      {
        error: "validation_error",
        detail: "Indique hasta qué fecha se puede oponer un tercero (AAAA-MM-DD).",
      },
      { status: 400 },
    );
  }

  const res = await fetch(
    `${API_V1}/eliminacion/lotes/${encodeURIComponent(params.loteId)}/publicar`,
    {
      method: "POST",
      headers: headersFor(locals, true),
      body: JSON.stringify({ publicado_hasta }),
    },
  );
  const data = await res.json().catch(() => ({}));
  return json(data, { status: res.status });
}
