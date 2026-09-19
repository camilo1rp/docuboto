// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin/flujos/reasignacion/+page.server.js — Carga SSR del universo de
 * usuarios para los selectores de origen/destino (T-08c `UserPicker`) y del
 * catálogo de dependencias activas para el origen "por dependencia".
 *
 * Reutiliza `GET /auth/users/pickable` (D-10: gateado con PERM_RADI en
 * auth-service, proyección mínima id+username), el mismo endpoint que
 * `/bandeja/api/users` usa para la cadena de visto bueno — no se toca ese
 * archivo (fuera de alcance), este `load` llama al gateway directamente.
 *
 * `GET /dependencias?activo=true` es el mismo endpoint que ya usa
 * `bandeja/+page.server.js` para su selector de dependencias — antes esta
 * pantalla pedía la dependencia de origen como texto libre ("nombre exacto…
 * (to_dept)"), lo que exponía el nombre de una columna del backend y hacía
 * que una errata silenciosa reasignara cero pasos o los equivocados. Ahora
 * se ofrece como desplegable sobre el catálogo real.
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";
import { mensajeError } from "$lib/utils/apiError.js";

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, fetch }) {
  const { user } = await parent();

  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";
  const userId = user?.sub ?? "";

  let users = [];
  // Reasignar en cascada sobre una lista recortada movería el trabajo a la
  // persona equivocada por no encontrar a la correcta.
  let usersIncompletos = false;
  let usersTotal = 0;
  let loadError = null;

  try {
    /** @type {Record<string, string>} */
    const headers = {
      Authorization: `Bearer ${token}`,
      "X-Tenant-Slug": tenant,
    };
    if (userId) headers["X-User-Id"] = userId;

    // `size=500` es el tope real del backend (`le=500`). Se pedían 200, que no
    // era ni el máximo ni una decisión: con más usuarios activos, quien no
    // entrara en la ventana no existía para quien busca — y en un selector eso
    // no se lee como una lista corta, se lee como que esa persona no está en el
    // sistema. `X-Total-Count` viaja para que la pantalla pueda decirlo.
    const res = await fetch(`${API_V1}/auth/users/pickable?size=500`, { headers });
    if (res.ok) {
      const raw = await res.json().catch(() => []);
      users = (Array.isArray(raw) ? raw : []).map((u) => ({ id: u.id, username: u.username }));
      const total = Number(res.headers.get("X-Total-Count"));
      usersIncompletos = Number.isFinite(total) && total > users.length;
      usersTotal = Number.isFinite(total) ? total : users.length;
    } else {
      const body = await res.json().catch(() => ({}));
      loadError = mensajeError(body, `Error ${res.status}`);
    }
  } catch {
    loadError = "No se pudo cargar la lista de usuarios.";
  }

  // Dependencias activas (mismo endpoint que bandeja/+page.server.js) — solo
  // hace falta para el desplegable de "origen por dependencia"; si falla no
  // se bloquea la carga de la pantalla (el origen "por usuario" sigue
  // funcionando), simplemente el desplegable queda vacío.
  let dependencias = [];
  try {
    const headers = {
      Authorization: `Bearer ${token}`,
      "X-Tenant-Slug": tenant,
    };
    if (userId) headers["X-User-Id"] = userId;
    const depRes = await fetch(`${API_V1}/dependencias?activo=true`, { headers });
    if (depRes.ok) dependencias = await depRes.json().catch(() => []);
  } catch {
    dependencias = [];
  }

  return {
    users,
    usersIncompletos,
    usersTotal,
    dependencias,
    loadError,
    // token y tenant NO se exponen: la ejecución del cliente usa
    // /admin/flujos/reasignacion/api* (D-05).
  };
}
