// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * pinarProxy.server.js — Fábricas de proxies SSR (D-05) para el CONTENIDO de un
 * plan PINAR: aspectos críticos, priorización, objetivos, proyectos,
 * seguimiento e instrumentos (RF-ADM-08; Ac. AGN 003/2015; Decreto 1080/2015
 * art. 2.8.2.5.8).
 *
 * **Por qué una fábrica y no once archivos con su propio cuerpo.** Los once
 * proxies que hacían falta son la misma función salvo por tres cosas: la ruta
 * aguas arriba, el método y **la allowlist de campos**. Copiarlos habría
 * duplicado once veces la parte que de verdad importa —la allowlist es la
 * barrera anti *mass-assignment*— y once copias de una barrera se desalinean en
 * silencio: basta que alguien añada un campo en una y no en las demás. Aquí la
 * allowlist es un parámetro, y el diff de un cambio se ve en un solo sitio.
 *
 * `headersFor` se **importa** del helper del circuito de instrumentos en vez de
 * volver a escribirse: ya hay dieciséis copias de esa función en las rutas del
 * proyecto y este no añade la diecisiete.
 */

import { json } from "@sveltejs/kit";

import { API_V1 } from "$lib/utils/config.server.js";
import { headersFor } from "$lib/server/trdCircuitoProxy.server.js";

export const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

function unauthenticated() {
  return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
}

function invalido(detail) {
  return json({ error: "validation_error", detail }, { status: 400 });
}

/**
 * Recorta el cuerpo a los campos admitidos.
 *
 * **Omitir NO es lo mismo que enviar `null`**, y la distinción importa: los
 * `PATCH` del PINAR interpretan un campo ausente como «no lo toques» y un
 * `null` como «bórralo». Un recorte que convirtiera lo ausente en `null`
 * borraría en silencio la mitad de un registro al editar un solo campo.
 *
 * @param {Record<string, unknown>} body
 * @param {string[]} permitidos
 */
export function recortar(body, permitidos) {
  /** @type {Record<string, unknown>} */
  const salida = {};
  for (const clave of permitidos) {
    if (Object.hasOwn(body ?? {}, clave)) salida[clave] = body[clave];
  }
  return salida;
}

/**
 * Proxy de una mutación sobre el contenido de un plan.
 *
 * @param {object} opts
 * @param {"POST"|"PATCH"|"PUT"|"DELETE"} opts.method
 * @param {(params: Record<string,string>) => string|null} opts.path
 *   Ruta aguas arriba, o `null` si algún identificador no es válido — un `null`
 *   se convierte en 400 SIN llamar al backend: un identificador que no es un
 *   UUID no se reenvía nunca, ni siquiera para que el backend lo rechace.
 * @param {string[]} [opts.campos] Allowlist. Ausente = no se envía cuerpo.
 * @param {(cuerpo: Record<string, unknown>) => string|null} [opts.validar]
 *   Devuelve el mensaje de error, o `null` si el cuerpo es aceptable.
 */
export function mutacion({ method, path, campos, validar }) {
  /** @type {import('@sveltejs/kit').RequestHandler} */
  return async function handler({ params, request, locals, fetch }) {
    if (!locals.accessToken) return unauthenticated();

    const destino = path(params);
    if (destino === null) return invalido("Identificador inválido.");

    /** @type {RequestInit} */
    const init = { method, headers: headersFor(locals, Boolean(campos)) };
    if (campos) {
      const body = await request.json().catch(() => ({}));
      const cuerpo = recortar(body ?? {}, campos);
      const problema = validar ? validar(cuerpo) : null;
      if (problema) return invalido(problema);
      init.body = JSON.stringify(cuerpo);
    }

    try {
      const res = await fetch(`${API_V1}/pinar${destino}`, init);
      // El 409 del ciclo de vida (`plan_no_editable`, `plan_no_rastreable`) se
      // reenvía CON su cuerpo: dice que el plan ya está aprobado y que hay que
      // reformular con una versión nueva, y colapsarlo a «no se pudo guardar»
      // mandaría a buscar un permiso a quien lo que tiene es un plan congelado.
      const data = res.status === 204 ? {} : await res.json().catch(() => ({}));
      return json(data, { status: res.status === 204 ? 200 : res.status });
    } catch {
      return json(
        { error: "connection_error", detail: "No se pudo conectar al servidor del PINAR." },
        { status: 503 },
      );
    }
  };
}

/**
 * Proxy de una LECTURA del PINAR (GET sin cuerpo ni query reenviada).
 *
 * Misma disciplina que `mutacion`: sin `locals.accessToken` → 401 antes de
 * nada, y un identificador que no es UUID (`path` devuelve `null`) → 400 sin
 * llamar al backend. El backend sigue decidiendo qué puede leer quien llama.
 *
 * @param {object} opts
 * @param {(params: Record<string,string>) => string|null} opts.path
 */
export function lectura({ path }) {
  /** @type {import('@sveltejs/kit').RequestHandler} */
  return async function handler({ params, locals, fetch }) {
    if (!locals.accessToken) return unauthenticated();

    const destino = path(params);
    if (destino === null) return invalido("Identificador inválido.");

    try {
      const res = await fetch(`${API_V1}/pinar${destino}`, { headers: headersFor(locals) });
      const data = await res.json().catch(() => ({}));
      return json(data, { status: res.status });
    } catch {
      return json(
        { error: "connection_error", detail: "No se pudo conectar al servidor del PINAR." },
        { status: 503 },
      );
    }
  };
}
