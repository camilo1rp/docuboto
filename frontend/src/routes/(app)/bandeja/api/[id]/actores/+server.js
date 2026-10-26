// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /bandeja/api/[id]/actores — los actores CONGELADOS del radicado (E01, RF-RAD-05).
 *
 * Por qué existe: radicar ya ata remitente y destinatarios al radicado, y hasta
 * ahora **ninguna pantalla los leía**. Un dato que se escribe y nadie mira no se
 * puede comprobar: el vínculo podía romperse sin que nada lo delatara, y una
 * Salida con tres destinatarios se veía exactamente igual que una con uno solo
 * —porque la lista se mostraba desde `sender_name`, que solo lleva el principal—.
 *
 * Lo que devuelve es **la copia congelada**, no la ficha vigente del catálogo:
 * responde «a quién iba dirigido este radicado», no «dónde vive hoy esa persona».
 * Gemelo de `/envios/api/actores`, que precarga el envío postal; este alimenta el
 * drawer de detalle.
 *
 * GET → ActorResponse[] (document-service; el clearance del llamante lo aplica el
 *   backend, que resuelve el radicado antes de listar sus actores).
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": locals.user?.tenant_slug ?? "",
    "X-Canal": "ui",
  };
  const userId = locals.user?.sub ?? "";
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(
      `${API_V1}/terceros/radicado/${encodeURIComponent(params.id)}/actores`,
      { headers },
    );
    const data = await res.json().catch(() => []);
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudieron cargar los actores del radicado." },
      { status: 503 },
    );
  }
}
