// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /correo — Los buzones que esta persona puede trabajar (E19).
 *
 * **La puerta que faltaba.** La bandeja de un buzón (`/correo/[cuentaId]`)
 * estaba escrita y gateada con `PERM_RADI` —quien trabaja el correo recibido es
 * la unidad de correspondencia—, pero la única entrada era un botón dentro de
 * `/admin/correo`, que exige `USUA_PERM_ADMIN`. El funcionario al que la
 * pantalla está dirigida no tenía forma de llegar a ella ni de saber el
 * identificador de su buzón. Capacidad construida, gateada para su público y
 * sin puerta: el patrón que más veces ha aparecido en este repositorio.
 *
 * El listado NO decide qué buzones se ven; eso lo decide el `WHERE` del
 * repositorio del backend, que reparte la cuenta personal a su dueño y la
 * institucional a los funcionarios asignados. Aquí solo se pinta lo que llegue.
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";
import { mensajeError } from "$lib/utils/apiError.js";

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, fetch }) {
  const { user } = await parent();

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${cookies.get(COOKIE_ACCESS_TOKEN) ?? ""}`,
    "X-Tenant-Slug": user?.tenant_slug ?? "",
  };

  // El error NO se descarta. Si el listado falla, la pantalla lo dice: una lista
  // vacía por un 403 y una lista vacía porque no hay buzones asignados se leen
  // igual, y llevan a conclusiones opuestas — «pide que te asignen un buzón» o
  // «avisa a soporte».
  try {
    const res = await fetch(`${API_V1}/email/cuentas`, { headers });
    if (res.ok) {
      const cuentas = await res.json().catch(() => []);
      return { cuentas: Array.isArray(cuentas) ? cuentas : [], cuentasError: null };
    }
    const cuerpo = await res.json().catch(() => ({}));
    return {
      cuentas: [],
      cuentasError: { detail: mensajeError(cuerpo, `Error ${res.status}`), status: res.status },
    };
  } catch {
    return {
      cuentas: [],
      cuentasError: { detail: "No se pudo conectar al servidor.", status: 503 },
    };
  }
}
