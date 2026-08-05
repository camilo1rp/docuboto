// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/trd/api/[id] — Proxy SSR: actualizar una serie TRD/CCD (D-05, E04).
 *
 * PATCH → el código (`code`) es inmutable (services/archive-service/app/
 *   routers/trd.py); solo se reenvían los campos editables de
 *   TrdSerieUpdate. USUA_PERM_TRD (min_crud=3) en el backend.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
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
      { error: "validation_error", detail: "Identificador de serie inválido." },
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
    retention_years,
    total_retention,
    disposition,
    is_active,
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

  /** @type {Record<string, unknown>} */
  const payload = {};
  if (typeof name === "string" && name.trim()) payload.name = name.trim();
  if (description !== undefined)
    payload.description =
      typeof description === "string" && description.trim() ? description.trim() : null;
  if (retention_years !== undefined && Number.isFinite(Number(retention_years)))
    payload.retention_years = Number(retention_years);
  if (total_retention !== undefined && Number.isFinite(Number(total_retention)))
    payload.total_retention = Number(total_retention);
  if (disposition !== undefined && DISPOSITIONS.has(disposition)) payload.disposition = disposition;
  if (typeof is_active === "boolean") payload.is_active = is_active;
  if (pdfa_profile !== undefined)
    payload.pdfa_profile = PDFA_PROFILES.has(pdfa_profile) ? pdfa_profile : null;

  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${locals.accessToken}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/trd/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers,
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo actualizar la serie." },
      { status: 503 },
    );
  }
}
