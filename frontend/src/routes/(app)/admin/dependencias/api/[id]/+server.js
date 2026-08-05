// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/dependencias/api/[id] — Proxy SSR: actualización parcial de una
 * dependencia (D-05, E14).
 *
 * PATCH → body con los campos a cambiar (codigo?, sigla?, nombre?,
 *   parent_id?, depe_rad_tp2?, activo?). Solo se reenvían las claves
 *   presentes en el body del cliente (edición parcial real, como
 *   `flujos/reglas/api/[id]`), nunca el objeto DependenciaNode completo con
 *   `children`/`created_at`/`updated_at` que la vista trae para pintar el
 *   árbol.
 *
 * NO se expone DELETE aquí: el brief de esta pantalla pide crear/editar/
 * desactivar, no borrado duro. El backend SÍ tiene `DELETE /dependencias/{id}`
 * (bloquea si tiene hijos) pero esta UI no lo usa — no hay proxy para una
 * acción que la pantalla no ofrece.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function PATCH({ locals, params, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const { id } = params;
  if (!id || !UUID_RE.test(id)) {
    return json(
      { error: "validation_error", detail: "Identificador de dependencia inválido." },
      {
        status: 400,
      },
    );
  }

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }
  if (!body || typeof body !== "object") {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  // Allowlist campo a campo — solo lo presente en el body del cliente.
  /** @type {Record<string, unknown>} */
  const payload = {};

  if ("codigo" in body) {
    const codigoNum = Number(body.codigo);
    if (!Number.isInteger(codigoNum) || codigoNum < 1 || codigoNum > 999999) {
      return json(
        { error: "validation_error", detail: "El código debe ser un entero entre 1 y 999999." },
        { status: 400 },
      );
    }
    payload.codigo = codigoNum;
  }
  if ("sigla" in body) {
    if (typeof body.sigla !== "string" || !body.sigla.trim()) {
      return json(
        { error: "validation_error", detail: "La sigla es obligatoria." },
        { status: 400 },
      );
    }
    payload.sigla = body.sigla.trim();
  }
  if ("nombre" in body) {
    if (typeof body.nombre !== "string" || !body.nombre.trim()) {
      return json(
        { error: "validation_error", detail: "El nombre es obligatorio." },
        { status: 400 },
      );
    }
    payload.nombre = body.nombre.trim();
  }
  if ("parent_id" in body) {
    const parentId = body.parent_id;
    if (parentId !== null && !UUID_RE.test(String(parentId ?? ""))) {
      return json(
        { error: "validation_error", detail: "La dependencia padre es inválida." },
        { status: 400 },
      );
    }
    payload.parent_id = parentId;
  }
  if ("depe_rad_tp2" in body) {
    payload.depe_rad_tp2 = body.depe_rad_tp2 === true;
  }
  if ("activo" in body) {
    payload.activo = body.activo === true;
  }

  if (Object.keys(payload).length === 0) {
    return json({ error: "validation_error", detail: "Nada que actualizar." }, { status: 400 });
  }

  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/dependencias/${encodeURIComponent(id)}`, {
      method: "PATCH",
      headers,
      body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo actualizar la dependencia." },
      { status: 503 },
    );
  }
}
