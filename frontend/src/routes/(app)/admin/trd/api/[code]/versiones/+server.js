// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/trd/api/[code]/versiones — Proxy SSR: historia de versiones de una
 * serie TRD + apertura de una versión nueva en borrador (ADR-025 Increment
 * 1/2, ADR-026 §"Superficie de API" pide el gemelo de este GET para TVD y
 * para TRD por igual).
 *
 * GET  → historia completa del código (`GET /api/v1/trd/{code}/versiones`).
 * POST → clona la versión vigente como `borrador` `version+1`. Body
 *   (allowlist, TrdSerieVersionCreate — todos opcionales, "clonar y
 *   sobre-escribir"): name, description, parent_id, retention_years,
 *   total_retention, disposition, archivo_gestion_years,
 *   archivo_central_years, pdfa_profile.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";
import { headersFor, makeVersionesGetHandler } from "$lib/server/trdCircuitoProxy.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DISPOSITIONS = new Set(["CT", "E", "S", "M", "eliminate", "conserve", "digitize", "select"]);
const PDFA_PROFILES = new Set(["1a", "1b", "2a", "2b", "2u", "3a", "3b", "3u"]);

export const GET = makeVersionesGetHandler("trd");

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, params, request }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const { code } = params ?? {};
  if (typeof code !== "string" || !code.trim()) {
    return json({ error: "validation_error", detail: "Código de serie TRD inválido." }, { status: 400 });
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
    parent_id,
    retention_years,
    total_retention,
    disposition,
    archivo_gestion_years,
    archivo_central_years,
    pdfa_profile,
  } = body ?? {};

  if (parent_id !== undefined && parent_id !== null && !UUID_RE.test(parent_id)) {
    return json({ error: "validation_error", detail: "Serie padre inválida." }, { status: 400 });
  }
  if (disposition !== undefined && disposition !== null && !DISPOSITIONS.has(disposition)) {
    return json({ error: "validation_error", detail: "Disposición final inválida." }, { status: 400 });
  }
  if (pdfa_profile !== undefined && pdfa_profile !== null && pdfa_profile !== "" && !PDFA_PROFILES.has(pdfa_profile)) {
    return json({ error: "validation_error", detail: "Perfil PDF/A inválido." }, { status: 400 });
  }

  /** @type {Record<string, unknown>} */
  const payload = {};
  if (typeof name === "string" && name.trim()) payload.name = name.trim();
  if (description !== undefined) {
    payload.description = typeof description === "string" && description.trim() ? description.trim() : null;
  }
  if (parent_id !== undefined) payload.parent_id = parent_id && UUID_RE.test(parent_id) ? parent_id : null;
  if (retention_years !== undefined && Number.isFinite(Number(retention_years))) {
    payload.retention_years = Number(retention_years);
  }
  if (total_retention !== undefined && Number.isFinite(Number(total_retention))) {
    payload.total_retention = Number(total_retention);
  }
  if (disposition !== undefined && DISPOSITIONS.has(disposition)) payload.disposition = disposition;
  if (archivo_gestion_years !== undefined && Number.isFinite(Number(archivo_gestion_years))) {
    payload.archivo_gestion_years = Number(archivo_gestion_years);
  }
  if (archivo_central_years !== undefined && Number.isFinite(Number(archivo_central_years))) {
    payload.archivo_central_years = Number(archivo_central_years);
  }
  if (pdfa_profile !== undefined) payload.pdfa_profile = PDFA_PROFILES.has(pdfa_profile) ? pdfa_profile : null;

  try {
    const res = await fetch(`${API_V1}/trd/${encodeURIComponent(code.trim())}/versiones`, {
      method: "POST",
      headers: headersFor(locals, true),
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json({ error: "connection_error", detail: "No se pudo crear la nueva versión." }, { status: 503 });
  }
}
