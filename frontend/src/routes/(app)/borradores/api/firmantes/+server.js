// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /borradores/api/firmantes — quién puede ir en un circuito de aprobación.
 *
 * Consume `GET /auth/users/pickable`, gateado con **PERM_RADI** en auth-service,
 * y NO el `/auth/users` de administración: componer un circuito es trabajo
 * ordinario de quien redacta un oficio, no una competencia de administrador.
 * Pedir `USUA_PERM_ADMIN` para elegir a un compañero de dependencia haría que la
 * función solo la pudiera usar quien administra el sistema — es decir, casi
 * nadie de los que producen documentos.
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
  if (locals.user?.sub) headers["X-User-Id"] = locals.user.sub;

  try {
    // `size=500` es el tope real del backend (`le=500`). Se pedían 200, que no
    // era ni el máximo ni una decisión: con más usuarios activos, quien no
    // entrara en la ventana no existía para quien busca — y en un selector eso
    // no se lee como una lista corta, se lee como que esa persona no está en el
    // sistema. `X-Total-Count` viaja para que la pantalla pueda decirlo.
    const res = await fetch(`${API_V1}/auth/users/pickable?size=500`, { headers });
    const data = await res.json().catch(() => []);
    const total = res.headers.get("X-Total-Count");
    return json(data, {
      status: res.status,
      headers: total ? { "X-Total-Count": total } : {},
    });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo cargar el listado de usuarios." },
      { status: 503 },
    );
  }
}
