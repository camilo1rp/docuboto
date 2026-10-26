// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/tvd/api — Proxy SSR: listar y crear agrupaciones de valoración TVD
 * (D-05, ADR-026 — fondo acumulado, Ac. AGN 001/2024 que compila el 004/2019).
 *
 * GET  ?is_active=&page=&size= → TvdSerieResponse[] (lee `v_tvd_agrupaciones`
 *   -- todas las versiones/estados de TVD, filtrables por `is_active`;
 *   X-Total-Count reenviado). Es el gemelo EXACTO de `GET /trd`: el backend
 *   (`app/routers/tvd.py`, espejo 1:1 de `trd.py`) no admite un parámetro
 *   `historico` -- no se inventa aquí uno que el gateway ignoraría en
 *   silencio.
 * POST                                     → crea una agrupación TVD
 *   versión 1 en `borrador`. USUA_PERM_TRD en el backend (services/
 *   archive-service/app/routers/tvd.py) — TVD reutiliza el permiso de TRD
 *   a propósito (ADR-026: misma potestad, mismo Comité).
 *
 * Body permitido (allowlist, anti mass-assignment, ADR-026 §"Superficie de
 * API"): code, name, description, parent_id, fondo_nombre,
 * fecha_extrema_inicial, fecha_extrema_final, justificacion_valoracion,
 * archivo_central_years (OBLIGATORIO, 0 es válido), disposition, pdfa_profile.
 * NUNCA total_retention (422, C-4: la TVD exige su fase central y el total es derivado).
 * NUNCA archivo_gestion_years, estado ni tipo_instrumento (los fija el
 * router del backend, nunca el cliente).
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
export async function GET({ locals, url }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  const params = new URLSearchParams();
  const isActive = url.searchParams.get("is_active");
  if (isActive === "true" || isActive === "false") params.set("is_active", isActive);
  const size = url.searchParams.get("size");
  params.set("size", size && /^\d+$/.test(size) ? size : "200");
  const page = url.searchParams.get("page");
  if (page && /^\d+$/.test(page)) params.set("page", page);

  try {
    const res = await fetch(`${API_V1}/tvd?${params}`, { headers: headersFor(locals) });
    const data = await res.json().catch(() => []);
    if (!res.ok) return json(data, { status: res.status });
    const total = res.headers.get("X-Total-Count") ?? String(Array.isArray(data) ? data.length : 0);
    return new Response(JSON.stringify(data), {
      status: 200,
      headers: { "Content-Type": "application/json", "X-Total-Count": total },
    });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de TVD." },
      { status: 503 },
    );
  }
}

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, request }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  const {
    code,
    name,
    description,
    parent_id,
    fondo_nombre,
    fecha_extrema_inicial,
    fecha_extrema_final,
    justificacion_valoracion,
    archivo_central_years,
    disposition,
    pdfa_profile,
  } = body ?? {};

  if (typeof code !== "string" || !code.trim()) {
    return json(
      { error: "validation_error", detail: "El código de la agrupación es obligatorio." },
      { status: 400 },
    );
  }
  if (typeof name !== "string" || !name.trim()) {
    return json(
      { error: "validation_error", detail: "El nombre de la agrupación es obligatorio." },
      { status: 400 },
    );
  }
  if (typeof fondo_nombre !== "string" || !fondo_nombre.trim()) {
    return json(
      { error: "validation_error", detail: "El fondo/productora es obligatorio en una TVD." },
      { status: 400 },
    );
  }
  if (typeof justificacion_valoracion !== "string" || !justificacion_valoracion.trim()) {
    return json(
      {
        error: "validation_error",
        detail: "La justificación de la valoración es obligatoria en una TVD.",
      },
      { status: 400 },
    );
  }
  if (typeof fecha_extrema_inicial !== "string" || !DATE_RE.test(fecha_extrema_inicial)) {
    return json(
      { error: "validation_error", detail: "La fecha extrema inicial es obligatoria (ISO date)." },
      { status: 400 },
    );
  }
  if (typeof fecha_extrema_final !== "string" || !DATE_RE.test(fecha_extrema_final)) {
    return json(
      { error: "validation_error", detail: "La fecha extrema final es obligatoria (ISO date)." },
      { status: 400 },
    );
  }
  if (fecha_extrema_final < fecha_extrema_inicial) {
    return json(
      {
        error: "validation_error",
        detail: "La fecha extrema final no puede ser anterior a la inicial.",
      },
      { status: 400 },
    );
  }
  if (parent_id !== undefined && parent_id !== null && !UUID_RE.test(parent_id)) {
    return json(
      { error: "validation_error", detail: "Agrupación padre inválida." },
      { status: 400 },
    );
  }
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
  if (aEnteroNoNegativo(archivo_central_years) === null) {
    return json(
      {
        error: "validation_error",
        detail: "Los años de archivo central son obligatorios (entero >= 0; 0 es válido).",
      },
      { status: 400 },
    );
  }

  const payload = {
    code: code.trim(),
    name: name.trim(),
    description: typeof description === "string" && description.trim() ? description.trim() : null,
    parent_id: parent_id && UUID_RE.test(parent_id) ? parent_id : null,
    fondo_nombre: fondo_nombre.trim(),
    fecha_extrema_inicial,
    fecha_extrema_final,
    justificacion_valoracion: justificacion_valoracion.trim(),
    archivo_central_years: aEnteroNoNegativo(archivo_central_years),
    disposition: DISPOSITIONS.has(disposition) ? disposition : "E",
    pdfa_profile: PDFA_PROFILES.has(pdfa_profile) ? pdfa_profile : null,
  };

  try {
    const res = await fetch(`${API_V1}/tvd`, {
      method: "POST",
      headers: headersFor(locals, true),
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo crear la agrupación TVD." },
      { status: 503 },
    );
  }
}
