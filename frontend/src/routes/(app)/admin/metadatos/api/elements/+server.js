// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/metadatos/api/elements — Proxy SSR: elementos de metadato
 * reutilizables (D-05, E03/RF-MET-03) —
 * services/document-service/app/routers/metadata_elements.py.
 *
 * GET  ?activo= → MetadataElementResponse[].
 * POST           → crea un elemento.
 *
 * HALLAZGO DE DOMINIO (ver +page.server.js): sin `require_permission` en el
 * backend; gate USUA_PERM_ADMIN aplicado solo del lado del cliente.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

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
export async function GET({ locals, url }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const params = new URLSearchParams();
  const activo = url.searchParams.get("activo");
  if (activo === "true" || activo === "false") params.set("activo", activo);

  try {
    const res = await fetch(`${API_V1}/metadata/elements?${params}`, { headers: headersFor(locals) });
    const data = await res.json().catch(() => []);
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de elementos de metadato." },
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

  const { clave, etiqueta, tipo_dato, longitud, ocurrencia_min, ocurrencia_max, modificable, orden, searchable, mapeo_dublin_core, activo } =
    body ?? {};

  if (typeof clave !== "string" || !clave.trim()) {
    return json({ error: "validation_error", detail: "La clave es obligatoria." }, { status: 400 });
  }
  if (typeof etiqueta !== "string" || !etiqueta.trim()) {
    return json({ error: "validation_error", detail: "La etiqueta es obligatoria." }, { status: 400 });
  }
  if (!TIPOS_DATO.has(tipo_dato)) {
    return json({ error: "validation_error", detail: "Tipo de dato inválido." }, { status: 400 });
  }

  const payload = {
    clave: clave.trim().slice(0, 100),
    etiqueta: etiqueta.trim().slice(0, 200),
    tipo_dato,
    longitud: Number.isInteger(Number(longitud)) && Number(longitud) >= 1 ? Number(longitud) : null,
    ocurrencia_min: Number.isInteger(Number(ocurrencia_min)) && Number(ocurrencia_min) >= 0 ? Number(ocurrencia_min) : 0,
    ocurrencia_max: Number.isInteger(Number(ocurrencia_max)) && Number(ocurrencia_max) >= 1 ? Number(ocurrencia_max) : null,
    modificable: modificable !== false,
    orden: Number.isInteger(Number(orden)) ? Number(orden) : 0,
    searchable: searchable === true,
    mapeo_dublin_core: typeof mapeo_dublin_core === "string" && mapeo_dublin_core.trim() ? mapeo_dublin_core.trim().slice(0, 100) : null,
    activo: activo !== false,
  };

  try {
    const res = await fetch(`${API_V1}/metadata/elements`, {
      method: "POST",
      headers: headersFor(locals, true),
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json({ error: "connection_error", detail: "No se pudo crear el elemento de metadato." }, { status: 503 });
  }
}
