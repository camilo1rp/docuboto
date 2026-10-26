// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /bandeja/api/[id]/historial-acceso — quién leyó y quién descargó (RF-BUS-10).
 *
 * La traza existía desde siempre —`document.radicado_consultado` al abrir la
 * ficha, `document.anexo_descargado` al bajar un fichero, las dos con actor,
 * canal e instante— y no había forma de pedirla desde el radicado: solo desde
 * `/admin/auditoria`, que exige ser administrador y saber por qué acción
 * filtrar. La respuesta estaba guardada y no se podía hacer la pregunta.
 *
 * El no-read-up lo aplica el backend ANTES de tocar `audit_log`: un radicado
 * por encima del clearance de quien mira responde 404, indistinguible de uno
 * que no existe. Este proxy no decide nada de eso, solo transporta.
 */

import { json } from "@sveltejs/kit";

import { API_V1 } from "$lib/utils/config.server.js";

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, params, url, fetch }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": locals.user?.tenant_slug ?? "",
  };
  const userId = locals.user?.sub ?? "";
  if (userId) headers["X-User-Id"] = userId;

  const size = Math.min(Number(url.searchParams.get("size") ?? 20) || 20, 100);
  try {
    const res = await fetch(
      `${API_V1}/documents/${encodeURIComponent(params.id)}/historial-acceso?size=${size}`,
      { headers },
    );
    const cuerpo = await res.json().catch(() => null);
    // `X-Total-Count` viaja: sin él la pantalla no puede decir «se muestran 20
    // de 137», y un historial truncado sin avisar se lee como un historial
    // completo — que sobre una pista de acceso es una afirmación falsa.
    const total = res.headers.get("X-Total-Count");
    return json(cuerpo, {
      status: res.status,
      headers: total ? { "X-Total-Count": total } : {},
    });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo leer el historial de acceso." },
      { status: 503 },
    );
  }
}
