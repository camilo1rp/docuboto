// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/trd/api — Proxy SSR: listar y crear series TRD/CCD (D-05, E04).
 *
 * GET  ?is_active=&page=&size= → TrdSerieResponse[] (X-Total-Count reenviado).
 * POST                          → crea una serie. USUA_PERM_TRD en el backend
 *   (services/archive-service/app/routers/trd.py).
 *
 * Body permitido (allowlist, anti mass-assignment): code, name, description,
 * disposition, parent_id, archivo_gestion_years, archivo_central_years
 * (ambas obligatorias, sin valor por omisión; 0 es válido), pdfa_profile y
 * dependencias_productoras (int[] de `dependencias.codigo`). NUNCA
 * retention_years ni total_retention: son derivados de solo lectura y el
 * backend los rechaza con 422 (C-4, ADR-040).
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";
import { leerFases, leerProductoras } from "$lib/server/trdFases.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const DISPOSITIONS = new Set(["CT", "E", "S", "M", "eliminate", "conserve", "digitize", "select"]);
const PDFA_PROFILES = new Set(["1a", "1b", "2a", "2b", "2u", "3a", "3b", "3u"]);

function headersFor(locals, withContentType = false) {
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  /** @type {Record<string, string>} */
  const headers = { Authorization: `Bearer ${locals.accessToken}`, "X-Tenant-Slug": tenant };
  if (userId) headers["X-User-Id"] = userId;
  if (withContentType) headers["Content-Type"] = "application/json";
  return headers;
}

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, url }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  const params = new URLSearchParams();
  const isActive = url.searchParams.get("is_active");
  if (isActive === "true" || isActive === "false") params.set("is_active", isActive);
  // Borradores, aprobadas y derogadas (con acta y autor): el backend lo exige
  // con USUA_PERM_TRD y responde 403 sin él (frontera de lectura, C-4).
  if (url.searchParams.get("incluir_en_tramite") === "true")
    params.set("incluir_en_tramite", "true");
  const size = url.searchParams.get("size");
  params.set("size", size && /^\d+$/.test(size) ? size : "200");
  const page = url.searchParams.get("page");
  if (page && /^\d+$/.test(page)) params.set("page", page);

  try {
    const res = await fetch(`${API_V1}/trd?${params}`, { headers: headersFor(locals) });
    const data = await res.json().catch(() => []);
    if (!res.ok) return json(data, { status: res.status });
    const total = res.headers.get("X-Total-Count") ?? String(Array.isArray(data) ? data.length : 0);
    return new Response(JSON.stringify(data), {
      status: 200,
      headers: { "Content-Type": "application/json", "X-Total-Count": total },
    });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de TRD." },
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
    disposition,
    parent_id,
    pdfa_profile,
    dependencias_productoras,
  } = body ?? {};

  if (typeof code !== "string" || !code.trim()) {
    return json(
      { error: "validation_error", detail: "El código de la serie es obligatorio." },
      { status: 400 },
    );
  }
  if (typeof name !== "string" || !name.trim()) {
    return json(
      { error: "validation_error", detail: "El nombre de la serie es obligatorio." },
      { status: 400 },
    );
  }
  if (parent_id !== undefined && parent_id !== null && !UUID_RE.test(parent_id)) {
    return json({ error: "validation_error", detail: "Serie padre inválida." }, { status: 400 });
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

  const fases = leerFases(body ?? {}, true);
  if (fases.error) return fases.error;
  /** @type {number[]|undefined} */
  let productoras;
  if (dependencias_productoras !== undefined && dependencias_productoras !== null) {
    const p = leerProductoras(dependencias_productoras);
    if (p.error) return p.error;
    productoras = p.value;
  }

  const payload = {
    code: code.trim(),
    name: name.trim(),
    description: typeof description === "string" && description.trim() ? description.trim() : null,
    disposition: DISPOSITIONS.has(disposition) ? disposition : "E",
    parent_id: parent_id && UUID_RE.test(parent_id) ? parent_id : null,
    ...fases.payload,
    pdfa_profile: PDFA_PROFILES.has(pdfa_profile) ? pdfa_profile : null,
    ...(productoras !== undefined ? { dependencias_productoras: productoras } : {}),
  };

  try {
    const res = await fetch(`${API_V1}/trd`, {
      method: "POST",
      headers: headersFor(locals, true),
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo crear la serie." },
      { status: 503 },
    );
  }
}
