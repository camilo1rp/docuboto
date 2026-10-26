// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /asistente/api/alcance — Marco de alcance del usuario (D-05).
 *
 * `GET /api/v1/assistant/alcance` → qué puede hacer esta persona, qué puede
 * consultar, y qué no puede **con su vía alternativa**.
 *
 * Por qué la interfaz lo pide y no se limita a que lo sepa el asistente: el
 * marco existe para que nadie descubra un límite fallando. Si solo lo conoce el
 * modelo, la persona tiene que preguntar para enterarse — y quien no pregunta se
 * queda esperando una acción que sus credenciales no permiten.
 *
 * Degradación honesta: si el marco no se puede consultar se dice, en vez de
 * pintar una lista vacía que se leería como «no puede hacer nada». Un panel de
 * capacidades vacío por un fallo de red es indistinguible de un usuario sin
 * permisos, y esa confusión manda a la gente a pedirle al administrador algo que
 * ya tiene.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals }) {
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

  try {
    const res = await fetch(`${API_V1}/assistant/alcance`, { headers });
    if (res.status === 401) {
      return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
    }
    if (!res.ok) {
      return json(
        {
          error: "alcance_unavailable",
          detail: "No se pudo consultar qué acciones tiene disponibles.",
        },
        { status: 503 },
      );
    }
    return json(await res.json(), { status: 200 });
  } catch {
    return json(
      {
        error: "alcance_unavailable",
        detail: "No se pudo consultar qué acciones tiene disponibles.",
      },
      { status: 503 },
    );
  }
}
