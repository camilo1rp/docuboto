// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Proxy BFF: combinación de campos de una plantilla (E23, RF-PRO-02).
 *
 * POST → POST /api/v1/plantillas/{id}/combinar
 *
 * La combinación NO baja al navegador a propósito. Un marcador resuelto en el
 * cliente es un dato que el cliente puede cambiar, y el documento que da fe no
 * puede depender de eso (D-05). Aquí solo se reenvía el contexto y se SANEA el
 * HTML que vuelve, igual que hace el listado de borradores.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";
import { attachSafeCuerpo } from "$lib/server/sanitizeHtml.js";

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, params, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  const payload = {
    radicado_padre_id: body?.radicado_padre_id || null,
    dest_dept_code: Number.isInteger(body?.dest_dept_code) ? body.dest_dept_code : null,
  };

  try {
    const res = await fetch(`${API_V1}/plantillas/${encodeURIComponent(params.id)}/combinar`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
      body: JSON.stringify(payload),
    });

    const data = await res.json().catch(() => ({}));
    // `cuerpo_html` lo reescribe SIEMPRE el saneo, ignorando lo que llegue del
    // gateway: mismo criterio que el listado de borradores (D-05).
    if (res.ok) attachSafeCuerpo(data);
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "gateway_unreachable", detail: "No se pudo contactar el servicio." },
      { status: 502 },
    );
  }
}
