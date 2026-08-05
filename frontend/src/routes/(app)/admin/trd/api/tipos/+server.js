// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/trd/api/tipos — Proxy SSR: listar y crear tipos documentales
 * (D-05, E03/RF-MET-07) — services/archive-service/app/routers/tipos_documentales.py.
 *
 * GET  ?trd_serie_id=&activo= → TipoDocumentalResponse[] (sin paginar).
 * POST                         → crea un tipo. USUA_PERM_TRD en el backend.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

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
  const serieId = url.searchParams.get("trd_serie_id");
  if (serieId && UUID_RE.test(serieId)) params.set("trd_serie_id", serieId);
  const activo = url.searchParams.get("activo");
  if (activo === "true" || activo === "false") params.set("activo", activo);

  try {
    const res = await fetch(`${API_V1}/tipos-documentales?${params}`, {
      headers: headersFor(locals),
    });
    const data = await res.json().catch(() => []);
    return json(data, { status: res.status });
  } catch {
    return json(
      {
        error: "connection_error",
        detail: "No se pudo conectar al servidor de tipos documentales.",
      },
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

  const { code, nombre, trd_serie_id, obligatorio, metadata_template, activo } = body ?? {};

  if (typeof code !== "string" || !code.trim()) {
    return json(
      { error: "validation_error", detail: "El código del tipo es obligatorio." },
      { status: 400 },
    );
  }
  if (typeof nombre !== "string" || !nombre.trim()) {
    return json(
      { error: "validation_error", detail: "El nombre del tipo es obligatorio." },
      { status: 400 },
    );
  }
  if (
    trd_serie_id !== undefined &&
    trd_serie_id !== null &&
    trd_serie_id !== "" &&
    !UUID_RE.test(trd_serie_id)
  ) {
    return json({ error: "validation_error", detail: "Serie TRD inválida." }, { status: 400 });
  }

  const payload = {
    code: code.trim(),
    nombre: nombre.trim(),
    trd_serie_id: trd_serie_id && UUID_RE.test(trd_serie_id) ? trd_serie_id : null,
    obligatorio: obligatorio === true,
    metadata_template:
      typeof metadata_template === "string" && metadata_template.trim()
        ? metadata_template.trim()
        : null,
    activo: activo !== false,
  };

  try {
    const res = await fetch(`${API_V1}/tipos-documentales`, {
      method: "POST",
      headers: headersFor(locals, true),
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo crear el tipo documental." },
      { status: 503 },
    );
  }
}
