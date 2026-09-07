// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /borradores/api/[id]/radicar — Proxy SSR para radicar un borrador aprobado (D-05, T-12c).
 *
 * POST → transición aprobado → radicado; devuelve DocumentResponse (201) con
 *   `tracking_number` del radicado real generado.
 *   El backend devuelve 409 si el borrador ya fue radicado.
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
export async function POST({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/borradores/${encodeURIComponent(params.id)}/radicar`, {
      method: "POST",
      headers,
    });

    const data = await res.json().catch(() => ({}));
    // El resultado es un DocumentResponse — no tiene campo cuerpo_html (es un radicado real).
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de radicación." },
      { status: 503 },
    );
  }
}
