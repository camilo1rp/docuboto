// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin/correo/+page.server.js — Configuración de la ingesta de correo (E19).
 *
 * Carga los buzones de la entidad y la aplicación OAuth con la que se accede a
 * Google o Microsoft. Ninguna de las dos lecturas ejecuta nada: radicar se hace
 * en la bandeja de cada buzón, correo por correo.
 *
 * Hubo aquí un tercer bloque —el estado del buzón único del despliegue y el
 * historial de sus ejecuciones— que desapareció con la ruta heredada `IMAP_*`.
 * El registro de qué se radicó desde correo no se pierde: cada radicado deja su
 * asiento en la auditoría inmutable, y la bandeja de cada cuenta conserva qué
 * mensaje produjo qué número.
 *
 * Gate: `USUA_PERM_ADMIN`, el mismo de los endpoints de cuentas.
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";
import { mensajeError } from "$lib/utils/apiError.js";

/**
 * Trae una lista y devuelve `[datos, error]`, sin lanzar.
 *
 * El error conserva el `status`: la pantalla distingue «no tienes permiso» de
 * «el servicio no respondió», y ninguno de los dos puede presentarse como «no
 * hay nada configurado» — ese fue un defecto real de `/envios`.
 *
 * @param {string} url
 * @param {Record<string, string>} headers
 * @param {typeof globalThis.fetch} fetch
 * @param {any[]} vacio
 * @returns {Promise<[any[], {detail: string, status: number} | null]>}
 */
async function traer(url, headers, fetch, vacio) {
  try {
    const res = await fetch(url, { headers });
    if (res.ok) {
      const crudo = await res.json().catch(() => vacio);
      return [Array.isArray(crudo) ? crudo : vacio, null];
    }
    const cuerpo = await res.json().catch(() => ({}));
    return [vacio, { detail: mensajeError(cuerpo, `Error ${res.status}`), status: res.status }];
  } catch {
    return [vacio, { detail: "No se pudo conectar al servicio de radicación.", status: 503 }];
  }
}

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, fetch }) {
  const { user } = await parent();

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${cookies.get(COOKIE_ACCESS_TOKEN) ?? ""}`,
    "X-Tenant-Slug": user?.tenant_slug ?? "",
  };
  const userId = user?.sub ?? "";
  if (userId) headers["X-User-Id"] = userId;

  // Cuentas y aplicaciones OAuth. Cada faceta lleva su propio error: un fallo
  // al listar las aplicaciones no debe ocultar las cuentas que sí se leyeron, ni
  // al revés — es el patrón que ya usa `/admin/notificaciones`.
  const [cuentas, cuentasError] = await traer(`${API_V1}/email/cuentas`, headers, fetch, []);
  const [apps, appsError] = await traer(`${API_V1}/email/apps-oauth`, headers, fetch, []);

  return { cuentas, cuentasError, apps, appsError };
}
