// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/grupos/api/[id]/permissions — Proxy SSR: permisos YA concedidos al
 * grupo, con su nivel CRUD (`GET /auth/groups/{id}/permissions`,
 * USUA_PERM_ADMIN).
 *
 * El espejo de lectura de `PUT .../permissions/{permission_id}`. Sin él, la
 * pantalla ofrecía el catálogo entero con todos los selectores en cero: no
 * distinguía "sin conceder" de "concedido con nivel 3", y guardar un permiso
 * cualquiera podía REBAJAR uno existente sin que nadie lo viera venir.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const { id } = params;
  if (!id || !UUID_RE.test(id)) {
    return json(
      { error: "validation_error", detail: "Identificador de grupo inválido." },
      { status: 400 },
    );
  }

  try {
    const res = await fetch(`${API_V1}/auth/groups/${encodeURIComponent(id)}/permissions`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": locals.user?.tenant_slug ?? "",
      },
    });
    const data = await res.json().catch(() => []);
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo obtener los permisos del grupo." },
      { status: 503 },
    );
  }
}
