// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/catalogos/api/[catalogo] — Proxy SSR: items de un catálogo de
 * referencia (D-05, E14) — services/tenant-service/app/routers/catalogos.py.
 *
 * GET  ?activo= → CatalogoItemResponse[]. El backend resuelve `catalogo`
 *   contra su propia whitelist `CATALOGS` (404 si no existe) — este proxy no
 *   duplica esa whitelist, solo evita mandar un segmento con caracteres que
 *   no pueden pertenecer a un nombre de catálogo real.
 * POST → crea un item. Body: { codigo, nombre, activo? }.
 *
 * HALLAZGO DE DOMINIO (ver +page.server.js): sin `require_permission` en el
 * backend; gate USUA_PERM_ADMIN aplicado solo del lado del cliente.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const CATALOGO_RE = /^[a-z0-9-]{1,60}$/;

function headersFor(locals, withContentType = false) {
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  /** @type {Record<string, string>} */
  const headers = { Authorization: `Bearer ${locals.accessToken}`, "X-Tenant-Slug": tenant };
  if (userId) headers["X-User-Id"] = userId;
  if (withContentType) headers["Content-Type"] = "application/json";
  return headers;
}

function badCatalogo() {
  return json(
    { error: "validation_error", detail: "Nombre de catálogo inválido." },
    { status: 400 },
  );
}

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, params, url }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  if (!CATALOGO_RE.test(params.catalogo)) return badCatalogo();

  const params2 = new URLSearchParams();
  const activo = url.searchParams.get("activo");
  if (activo === "true" || activo === "false") params2.set("activo", activo);

  try {
    const res = await fetch(
      `${API_V1}/catalogos/${encodeURIComponent(params.catalogo)}?${params2}`,
      {
        headers: headersFor(locals),
      },
    );
    const data = await res.json().catch(() => []);
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de catálogos." },
      { status: 503 },
    );
  }
}

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, params, request }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  if (!CATALOGO_RE.test(params.catalogo)) return badCatalogo();

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  const { codigo, nombre, activo } = body ?? {};
  if (typeof codigo !== "string" || !codigo.trim()) {
    return json(
      { error: "validation_error", detail: "El código es obligatorio." },
      { status: 400 },
    );
  }
  if (typeof nombre !== "string" || !nombre.trim()) {
    return json(
      { error: "validation_error", detail: "El nombre es obligatorio." },
      { status: 400 },
    );
  }

  const payload = { codigo: codigo.trim(), nombre: nombre.trim(), activo: activo !== false };

  try {
    const res = await fetch(`${API_V1}/catalogos/${encodeURIComponent(params.catalogo)}`, {
      method: "POST",
      headers: headersFor(locals, true),
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo crear el item del catálogo." },
      { status: 503 },
    );
  }
}
