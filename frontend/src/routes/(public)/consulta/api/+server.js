// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /consulta/api — seguimiento ciudadano por código de verificación.
 *
 * **Sin sesión a propósito**: quien consulta no tiene cuenta ni tiene por qué
 * tenerla. El código es la credencial, y por eso el backend recorta a nivel
 * público (un código no alcanza un radicado clasificado).
 *
 * Pasa por el servidor —y no se llama al gateway desde el navegador— para que
 * la URL interna del gateway y el inquilino no viajen al cliente.
 */

import { json } from "@sveltejs/kit";
import { API_V1, PUBLIC_TENANT } from "$lib/utils/config.server.js";
import { codigoError, mensajeError } from "$lib/utils/apiError.js";

/** @type {import('./$types').RequestHandler} */
export async function GET({ url, fetch }) {
  if (!PUBLIC_TENANT) {
    return json(
      {
        error: "consulta_no_habilitada",
        detail: "Esta entidad no tiene habilitada la consulta en línea.",
      },
      { status: 503 },
    );
  }

  const codigo = (url.searchParams.get("codigo") ?? "").trim();
  // Se acota la forma antes de salir a la red: el código lo genera el sistema
  // (32 hexadecimales) y cualquier otra cosa es un error de tecleo, no una
  // consulta que merezca molestar al backend.
  if (!/^[0-9a-fA-F]{32}$/.test(codigo)) {
    return json(
      {
        error: "codigo_invalido",
        detail:
          "El código de verificación tiene 32 caracteres entre 0-9 y a-f. " +
          "Revíselo: es el que le entregamos al radicar.",
      },
      { status: 400 },
    );
  }

  try {
    const res = await fetch(
      `${API_V1}/public/${encodeURIComponent(PUBLIC_TENANT)}/verify/${encodeURIComponent(codigo)}`,
    );
    const cuerpo = await res.json().catch(() => null);
    if (res.status === 404) {
      // Neutro a propósito: el backend responde lo mismo para un código que no
      // existe y para uno de un radicado clasificado, para no confirmar su
      // existencia. La pantalla no debe deshacer esa indistinción.
      return json(
        {
          error: "not_found",
          detail: "No encontramos ningún trámite con ese código. Verifique que lo copió completo.",
        },
        { status: 404 },
      );
    }
    if (!res.ok) {
      return json(
        {
          error: codigoError(cuerpo) ?? "upstream_error",
          detail: mensajeError(cuerpo, `Error ${res.status}`),
        },
        { status: res.status },
      );
    }
    return json(cuerpo);
  } catch {
    return json(
      { error: "upstream_unavailable", detail: "No se pudo consultar en este momento." },
      { status: 502 },
    );
  }
}
