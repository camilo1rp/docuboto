// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Proxy BFF: visto bueno del turno activo (E23 Inc.4, RF-PRO-10).
 *
 * POST → POST /api/v1/signature/requests/{id}/vobo
 *
 * Aprobar NO es firmar, y por eso son dos rutas y no una con un parámetro. Un
 * visto bueno no compromete a la entidad ni produce el documento definitivo:
 * queda anclado a la huella del texto que se aprobó, y ya. Presentarlos igual
 * atribuiría a un revisor un compromiso que no adquirió.
 *
 * Aquí NO viaja ningún segundo factor, a diferencia de `sign`: es la decisión
 * D-6 del DS, y el hecho de que este proxy no tenga campo donde ponerlo es la
 * forma de que no se cuele por descuido.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, params, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";

  let body = {};
  try {
    body = (await request.json()) ?? {};
  } catch {
    // Cuerpo ausente o ilegible: el comentario es OPCIONAL (aprobar no exige
    // explicación, detener el trámite de otro sí), así que no es un error.
    body = {};
  }

  const comentario = typeof body.comentario === "string" ? body.comentario.trim() : "";

  try {
    const res = await fetch(`${API_V1}/signature/requests/${encodeURIComponent(params.id)}/vobo`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
      body: JSON.stringify({ comentario: comentario || null }),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "gateway_unreachable", detail: "No se pudo contactar el servicio." },
      { status: 502 },
    );
  }
}
