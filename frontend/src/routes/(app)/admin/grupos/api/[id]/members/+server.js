// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/grupos/api/[id]/members — Proxy SSR: miembros ACTUALES del grupo
 * (`GET /auth/groups/{id}/members`, USUA_PERM_ADMIN).
 *
 * Cierra una administración a ciegas: la pantalla daba de alta y de baja
 * miembros sin poder mostrar quién estaba ya en el grupo, y llevaba en su lugar
 * una lista "de sesión" con lo que ese mismo drawer había agregado — que no es
 * la membresía, es el recuerdo de los clics. Quien administra permisos no
 * puede trabajar sin ver lo que ya concedió.
 *
 * El endpoint EXISTÍA desde el espejo de lectura de RBAC; lo que faltaba era
 * consumirlo. El comentario del proxy vecino afirmaba lo contrario ("GAP DE
 * BACKEND"), y esa frase falsa es lo que mantuvo el hueco abierto.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, params, url }) {
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

  // El backend pagina (`page`/`size`, X-Total-Count). Se propaga el tamaño para
  // que un grupo grande no se muestre truncado en silencio: la cuenta real
  // viaja en la cabecera y la pantalla la enseña.
  const size = Math.min(Math.max(Number(url.searchParams.get("size") ?? 100) || 100, 1), 100);
  const page = Math.max(Number(url.searchParams.get("page") ?? 1) || 1, 1);

  try {
    const res = await fetch(
      `${API_V1}/auth/groups/${encodeURIComponent(id)}/members?page=${page}&size=${size}`,
      {
        headers: {
          Authorization: `Bearer ${token}`,
          "X-Tenant-Slug": locals.user?.tenant_slug ?? "",
        },
      },
    );
    const data = await res.json().catch(() => []);
    const total = res.headers.get("X-Total-Count");
    return json(data, {
      status: res.status,
      headers: total ? { "X-Total-Count": total } : {},
    });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo obtener los miembros del grupo." },
      { status: 503 },
    );
  }
}
