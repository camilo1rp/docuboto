// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/plantillas/api/[id]/alcance — a qué dependencias alcanza una plantilla
 * (E23 Inc.5, RF-PRO-06; paridad con `sgd_plan_plantillas.depe_codi`).
 *
 * PUT → PUT /api/v1/plantillas/{id}/alcance
 *
 * No es una cuestión de seguridad —una plantilla no contiene datos de nadie—
 * sino de uso: con cuarenta plantillas en la lista, la que hace falta no se
 * encuentra y el camino corto es no usar ninguna.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

/** @type {import('./$types').RequestHandler} */
export async function PUT({ locals, params, request }) {
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

  const esGlobal = body?.es_global === true;
  const dependencias = Array.isArray(body?.dependencias)
    ? body.dependencias.filter((d) => Number.isInteger(d))
    : [];

  // Restringir a NINGUNA dependencia es un estado válido —una plantilla
  // retirada de circulación sin borrar su historial— pero no puede ser un
  // descuido: se avisa desde la pantalla, y aquí se deja pasar a propósito.
  try {
    const res = await fetch(`${API_V1}/plantillas/${encodeURIComponent(params.id)}/alcance`, {
      method: "PUT",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
      // Global manda: si lo es, la lista se ignora. Mantenerla guardada dejaría
      // una asignación invisible que reaparecería al volver a restringir.
      body: JSON.stringify({ es_global: esGlobal, dependencias: esGlobal ? [] : dependencias }),
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
