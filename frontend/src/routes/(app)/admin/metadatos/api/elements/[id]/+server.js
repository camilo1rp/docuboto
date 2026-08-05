// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/metadatos/api/elements/[id] — Proxy SSR: actualizar/borrar un
 * elemento de metadato reutilizable (D-05, E03/RF-MET-03).
 *
 * Gate real (revisión UX 2026-08-02, punto 2 — corrige un hallazgo previo
 * que afirmaba lo contrario): PATCH exige `USUA_PERM_TRD`, DELETE exige
 * `USUA_PERM_TRD` con `min_crud=3`
 * (`services/document-service/app/routers/metadata_elements.py`).
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const TIPOS_DATO = new Set(["text", "number", "date", "boolean", "select"]);

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
export async function PATCH({ locals, params, request }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const { id } = params;
  if (!id || !UUID_RE.test(id)) {
    return json(
      { error: "validation_error", detail: "Identificador de elemento inválido." },
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
    etiqueta,
    tipo_dato,
    longitud,
    ocurrencia_min,
    ocurrencia_max,
    modificable,
    orden,
    searchable,
    mapeo_dublin_core,
    activo,
  } = body ?? {};
  if (tipo_dato !== undefined && !TIPOS_DATO.has(tipo_dato)) {
    return json({ error: "validation_error", detail: "Tipo de dato inválido." }, { status: 400 });
  }

  /** @type {Record<string, unknown>} */
  const payload = {};
  if (typeof etiqueta === "string" && etiqueta.trim())
    payload.etiqueta = etiqueta.trim().slice(0, 200);
  if (tipo_dato !== undefined && TIPOS_DATO.has(tipo_dato)) payload.tipo_dato = tipo_dato;
  if (longitud !== undefined)
    payload.longitud =
      Number.isInteger(Number(longitud)) && Number(longitud) >= 1 ? Number(longitud) : null;
  if (ocurrencia_min !== undefined && Number.isInteger(Number(ocurrencia_min)))
    payload.ocurrencia_min = Number(ocurrencia_min);
  if (ocurrencia_max !== undefined)
    payload.ocurrencia_max =
      Number.isInteger(Number(ocurrencia_max)) && Number(ocurrencia_max) >= 1
        ? Number(ocurrencia_max)
        : null;
  if (typeof modificable === "boolean") payload.modificable = modificable;
  if (orden !== undefined && Number.isInteger(Number(orden))) payload.orden = Number(orden);
  if (typeof searchable === "boolean") payload.searchable = searchable;
  if (mapeo_dublin_core !== undefined)
    payload.mapeo_dublin_core =
      typeof mapeo_dublin_core === "string" && mapeo_dublin_core.trim()
        ? mapeo_dublin_core.trim().slice(0, 100)
        : null;
  if (typeof activo === "boolean") payload.activo = activo;

  try {
    const res = await fetch(`${API_V1}/metadata/elements/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers: headersFor(locals, true),
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo actualizar el elemento de metadato." },
      { status: 503 },
    );
  }
}

/** @type {import('./$types').RequestHandler} */
export async function DELETE({ locals, params }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const { id } = params;
  if (!id || !UUID_RE.test(id)) {
    return json(
      { error: "validation_error", detail: "Identificador de elemento inválido." },
      { status: 400 },
    );
  }

  try {
    const res = await fetch(`${API_V1}/metadata/elements/${encodeURIComponent(id)}`, {
      method: "DELETE",
      headers: headersFor(locals),
    });
    if (res.status === 204) return new Response(null, { status: 204 });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo eliminar el elemento de metadato." },
      { status: 503 },
    );
  }
}
