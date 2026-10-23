// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/tvd/api/[id] — Proxy SSR: actualizar una agrupación TVD (D-05,
 * ADR-026).
 *
 * PATCH → solo aplicable EN SITIO cuando la versión está en `borrador`
 *   (`pdfa_profile` es la excepción, aceptado en cualquier estado — mismo
 *   patrón que TRD/ADR-025). El código es inmutable. USUA_PERM_TRD
 *   (min_crud=3) en el backend.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";
import { aEnteroNoNegativo } from "$lib/server/trdFases.server.js";
import { headersFor } from "$lib/server/trdCircuitoProxy.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const DISPOSITIONS = new Set(["CT", "E", "S", "M", "eliminate", "conserve", "digitize", "select"]);
const PDFA_PROFILES = new Set(["1a", "1b", "2a", "2b", "2u", "3a", "3b", "3u"]);

/** @type {import('./$types').RequestHandler} */
export async function PATCH({ locals, params, request }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const { id } = params;
  if (!id || !UUID_RE.test(id)) {
    return json(
      { error: "validation_error", detail: "Identificador de agrupación inválido." },
      { status: 400 },
    );
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  const {
    name,
    description,
    fondo_nombre,
    fecha_extrema_inicial,
    fecha_extrema_final,
    justificacion_valoracion,
    archivo_central_years,
    disposition,
    pdfa_profile,
  } = body ?? {};

  if (disposition !== undefined && disposition !== null && !DISPOSITIONS.has(disposition)) {
    return json(
      { error: "validation_error", detail: "Disposición final inválida." },
      { status: 400 },
    );
  }
  if (
    pdfa_profile !== undefined &&
    pdfa_profile !== null &&
    pdfa_profile !== "" &&
    !PDFA_PROFILES.has(pdfa_profile)
  ) {
    return json({ error: "validation_error", detail: "Perfil PDF/A inválido." }, { status: 400 });
  }
  if (
    fecha_extrema_inicial !== undefined &&
    fecha_extrema_inicial !== null &&
    !DATE_RE.test(fecha_extrema_inicial)
  ) {
    return json(
      { error: "validation_error", detail: "Fecha extrema inicial inválida (ISO date)." },
      { status: 400 },
    );
  }
  if (
    fecha_extrema_final !== undefined &&
    fecha_extrema_final !== null &&
    !DATE_RE.test(fecha_extrema_final)
  ) {
    return json(
      { error: "validation_error", detail: "Fecha extrema final inválida (ISO date)." },
      { status: 400 },
    );
  }
  if (
    fecha_extrema_inicial &&
    fecha_extrema_final &&
    DATE_RE.test(fecha_extrema_inicial) &&
    DATE_RE.test(fecha_extrema_final) &&
    fecha_extrema_final < fecha_extrema_inicial
  ) {
    return json(
      {
        error: "validation_error",
        detail: "La fecha extrema final no puede ser anterior a la inicial.",
      },
      { status: 400 },
    );
  }

  /** @type {Record<string, unknown>} */
  const payload = {};
  if (typeof name === "string" && name.trim()) payload.name = name.trim();
  if (description !== undefined) {
    payload.description =
      typeof description === "string" && description.trim() ? description.trim() : null;
  }
  if (typeof fondo_nombre === "string" && fondo_nombre.trim())
    payload.fondo_nombre = fondo_nombre.trim();
  if (fecha_extrema_inicial !== undefined && DATE_RE.test(fecha_extrema_inicial)) {
    payload.fecha_extrema_inicial = fecha_extrema_inicial;
  }
  if (fecha_extrema_final !== undefined && DATE_RE.test(fecha_extrema_final)) {
    payload.fecha_extrema_final = fecha_extrema_final;
  }
  if (typeof justificacion_valoracion === "string" && justificacion_valoracion.trim()) {
    payload.justificacion_valoracion = justificacion_valoracion.trim();
  }
  if (archivo_central_years !== undefined && archivo_central_years !== null) {
    const n = aEnteroNoNegativo(archivo_central_years);
    if (n === null) {
      return json(
        {
          error: "validation_error",
          detail: "Los años de archivo central deben ser un entero mayor o igual a 0.",
        },
        { status: 400 },
      );
    }
    payload.archivo_central_years = n;
  }
  if (disposition !== undefined && DISPOSITIONS.has(disposition)) payload.disposition = disposition;
  if (pdfa_profile !== undefined)
    payload.pdfa_profile = PDFA_PROFILES.has(pdfa_profile) ? pdfa_profile : null;

  try {
    const res = await fetch(`${API_V1}/tvd/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: headersFor(locals, true),
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo actualizar la agrupación TVD." },
      { status: 503 },
    );
  }
}
