// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Proxy SSR de `POST /api/v1/eliminacion/lotes/{id}/ejecutar` (RF-TRD-07, fase 5).
 *
 * **Este proxy destruye documentos de archivo.**
 *
 * No reenvía ningún cuerpo, y no por descuido: en una destrucción no hay nada
 * que el cliente pueda parametrizar. Qué se destruye lo dice el inventario
 * congelado al proponer; bajo qué acto, el aval del Comité. Un `dry_run` o un
 * `forzar` serían justo los parámetros que un día alguien pasa mal — y aquí no
 * hay vuelta atrás.
 *
 * El timeout es largo porque purgar un expediente con cientos de anexos implica
 * otras tantas llamadas a MinIO aguas abajo: cortar a mitad dejaría el estado a
 * medias que todo el diseño intenta evitar.
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
    `${API_V1}/eliminacion/lotes/${encodeURIComponent(params.loteId)}/ejecutar`,
    { method: "POST", headers: headersFor(locals, true) },
  );
  const data = await res.json().catch(() => ({}));
  return json(data, { status: res.status });
}
