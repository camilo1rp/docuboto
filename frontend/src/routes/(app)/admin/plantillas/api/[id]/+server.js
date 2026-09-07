// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/plantillas/api/[id] — Proxy SSR para eliminar una plantilla (D-05, T-12c).
 *
 * DELETE → elimina la plantilla (204). Requiere USUA_PERM_ADMIN.
 *          El backend devuelve 404 si no existe o 403 si falta el permiso.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

/** @type {import('./$types').RequestHandler} */
// El identificador va CODIFICADO al construir la ruta hacia el gateway.
//
// Sin codificar, un `?`, un `#` o un `%00` en el segmento reescriben la
// consulta o el fragmento de la petición que este proxy emite CON EL TOKEN DEL
// USUARIO: la ruta que el navegador pidió y la que el gateway recibe dejan de
// ser la misma. El gateway ya rechaza esas rutas, pero eso es su defensa, no la
// de aquí — quien construye una URL con datos de fuera la codifica.
export async function DELETE({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";

  try {
    const res = await fetch(`${API_V1}/plantillas/${encodeURIComponent(params.id)}`, {
      method: "DELETE",
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
    });

    if (res.status === 204) {
      return new Response(null, { status: 204 });
    }

    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de plantillas." },
      { status: 503 },
    );
  }
}
