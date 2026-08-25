// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * admin/correo/+page.server.js — Ingesta de correo (E19).
 *
 * Carga dos cosas, y ninguna ejecuta la ingesta:
 *
 * 1. **El estado** (`GET /ingest/email/estado`): si hay buzón, cuál, y de quién.
 *    Este endpoint se construyó junto con la pantalla porque no existía: hasta
 *    entonces la única forma de saber si la ingesta estaba configurada era
 *    dispararla, y dispararla crea radicados oficiales y marca los correos como
 *    leídos. Una capacidad que solo se puede consultar usándola no se puede
 *    ofrecer en una pantalla.
 *
 * 2. **El historial** (`GET /audit?action=ingest.email.poll`): cada poll deja su
 *    asiento con quién lo disparó y qué números de radicado consumió. No hace
 *    falta endpoint propio — la auditoría inmutable ya es el registro, y leerla
 *    aquí evita un segundo libro que podría discrepar del primero.
 *
 * Gate: `USUA_PERM_ADMIN`, el mismo de ambos endpoints de `ingest.py` y el de
 * `/audit`.
 */

import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";
import { mensajeError } from "$lib/utils/apiError.js";

const HISTORIAL_SIZE = 20;

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

  let estado = null;
  let estadoError = null;
  try {
    const res = await fetch(`${API_V1}/ingest/email/estado`, { headers });
    if (res.ok) {
      estado = await res.json();
    } else {
      const body = await res.json().catch(() => ({}));
      estadoError = { detail: mensajeError(body, `Error ${res.status}`), status: res.status };
    }
  } catch {
    estadoError = { detail: "No se pudo conectar al servicio de radicación.", status: 503 };
  }

  // El historial es complementario: si falla, la pantalla sigue sirviendo para
  // ver el estado y disparar la ingesta. Por eso lleva su propio error y no
  // tumba el `load` entero.
  let historial = [];
  let historialError = null;
  try {
    const res = await fetch(
      `${API_V1}/audit?action=ingest.email.poll&page=1&size=${HISTORIAL_SIZE}`,
      { headers },
    );
    if (res.ok) {
      const crudo = await res.json().catch(() => []);
      historial = Array.isArray(crudo) ? crudo : [];
    } else {
      const body = await res.json().catch(() => ({}));
      historialError = { detail: mensajeError(body, `Error ${res.status}`), status: res.status };
    }
  } catch {
    historialError = { detail: "No se pudo consultar el registro de auditoría.", status: 503 };
  }

  // Cuentas y aplicaciones OAuth. Cada faceta lleva su propio error: un fallo
  // al listar las aplicaciones no debe ocultar las cuentas que sí se leyeron, ni
  // al revés — es el patrón que ya usa `/admin/notificaciones`.
  const [cuentas, cuentasError] = await traer(`${API_V1}/email/cuentas`, headers, fetch, []);
  const [apps, appsError] = await traer(`${API_V1}/email/apps-oauth`, headers, fetch, []);

  return {
    estado,
    estadoError,
    historial,
    historialError,
    cuentas,
    cuentasError,
    apps,
    appsError,
  };
}
