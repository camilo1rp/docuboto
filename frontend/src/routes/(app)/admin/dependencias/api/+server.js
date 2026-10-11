// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/dependencias/api — Proxy SSR: listado plano y creación de
 * dependencias (D-05, E14, `tenant-service/app/routers/dependencias.py`).
 *
 * GET  ?activo=&parent_id=  → DependenciaResponse[] plano (para el selector
 *      "dependencia padre" del formulario; la vista principal usa el árbol
 *      de ./tree).
 * POST → crea una dependencia. Body: { codigo, sigla, nombre, parent_id?,
 *        depe_rad_tp2?, activo? }.
 *
 * ADR-032: las dependencias se correlacionan por CÓDIGO en todo el sistema.
 * El código es inmutable tras crearla; renombrar es seguro. Desactivar con
 * trámite en curso lo rechaza el backend (409). Este proxy solo reenvía la
 * petición; la confirmación de desactivar vive en la UI.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

function headersFor(locals) {
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  /** @type {Record<string, string>} */
  const headers = { Authorization: `Bearer ${locals.accessToken}`, "X-Tenant-Slug": tenant };
  if (userId) headers["X-User-Id"] = userId;
  return headers;
}

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  const params = new URLSearchParams();
  const activo = url.searchParams.get("activo");
  if (activo === "true" || activo === "false") params.set("activo", activo);
  const parentId = url.searchParams.get("parent_id");
  if (parentId && UUID_RE.test(parentId)) params.set("parent_id", parentId);

  const qs = params.toString();
  try {
    const res = await fetch(`${API_V1}/dependencias${qs ? `?${qs}` : ""}`, {
      headers: headersFor(locals),
    });
    const data = await res.json().catch(() => []);
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de dependencias." },
      { status: 503 },
    );
  }
}

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  // Allowlist explícita — nunca reenviar el body íntegro (anti mass-assignment).
  const {
    codigo,
    sigla,
    nombre,
    parent_id: parentId,
    depe_rad_tp2: depeRadTp2,
    activo,
  } = body ?? {};

  const codigoNum = Number(codigo);
  if (!Number.isInteger(codigoNum) || codigoNum < 1 || codigoNum > 999999) {
    return json(
      { error: "validation_error", detail: "El código debe ser un entero entre 1 y 999999." },
      { status: 400 },
    );
  }
  if (typeof sigla !== "string" || !sigla.trim()) {
    return json({ error: "validation_error", detail: "La sigla es obligatoria." }, { status: 400 });
  }
  if (typeof nombre !== "string" || !nombre.trim()) {
    return json(
      { error: "validation_error", detail: "El nombre es obligatorio." },
      { status: 400 },
    );
  }
  if (parentId !== null && parentId !== undefined && !UUID_RE.test(String(parentId))) {
    return json(
      { error: "validation_error", detail: "La dependencia padre es inválida." },
      { status: 400 },
    );
  }

  const payload = {
    codigo: codigoNum,
    sigla: sigla.trim(),
    nombre: nombre.trim(),
    parent_id: parentId || null,
    depe_rad_tp2: depeRadTp2 === true,
    activo: activo !== false,
  };

  try {
    const res = await fetch(`${API_V1}/dependencias`, {
      method: "POST",
      headers: { ...headersFor(locals), "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo crear la dependencia." },
      { status: 503 },
    );
  }
}
