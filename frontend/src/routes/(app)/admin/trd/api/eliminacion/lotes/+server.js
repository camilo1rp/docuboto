// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/trd/api/eliminacion/lotes — Proxy SSR (D-05) de `POST /api/v1/eliminacion/lotes`.
 *
 * Propone un lote de eliminación (RF-TRD-07, fase 1 de 5). **No elimina nada.**
 *
 * El `409 lote_sin_expedientes_admisibles` **no es un error que ocultar**: trae
 * el detalle de por qué se rechazó cada expediente, y es justo lo que la
 * persona necesita ver. Se reenvía tal cual, con su cuerpo — un proxy que lo
 * colapsara a «no se pudo crear el lote» dejaría como única salida probar de
 * uno en uno.
 */
import { json } from "@sveltejs/kit";

import { API_V1 } from "$lib/utils/config.server.js";
import { headersFor } from "$lib/server/trdCircuitoProxy.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function POST({ request, locals, fetch }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const body = await request.json().catch(() => ({}));
  const { codigo, expediente_ids, observaciones } = body ?? {};

  if (typeof codigo !== "string" || codigo.trim().length < 3) {
    return json(
      { error: "validation_error", detail: "El código del lote es obligatorio." },
      { status: 400 },
    );
  }
  if (!Array.isArray(expediente_ids) || expediente_ids.length === 0) {
    return json(
      { error: "validation_error", detail: "Seleccione al menos un expediente." },
      { status: 400 },
    );
  }
  if (!expediente_ids.every((id) => typeof id === "string" && UUID_RE.test(id))) {
    return json(
      { error: "validation_error", detail: "Identificador de expediente inválido." },
      { status: 400 },
    );
  }

  /** @type {Record<string, unknown>} */
  const payload = { codigo: codigo.trim(), expediente_ids };
  if (typeof observaciones === "string" && observaciones.trim()) {
    payload.observaciones = observaciones.trim();
  }

  const res = await fetch(`${API_V1}/eliminacion/lotes`, {
    method: "POST",
    headers: headersFor(locals, true),
    body: JSON.stringify(payload),
  });
  const data = await res.json().catch(() => ({}));
  return json(data, { status: res.status });
}
