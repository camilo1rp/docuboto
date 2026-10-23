// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /radicacion/api/reto — reto antirrobots ALTCHA de la ventanilla, sin sesión.
 *
 * El widget del navegador lo pide aquí (nunca al gateway: regla D-05) y lo
 * resuelve con una prueba de trabajo local. El reto llega YA firmado por el
 * backend; la clave de la firma no pasa por el front. Nunca se cachea: un reto
 * es de un solo uso.
 */

import { json } from "@sveltejs/kit";
import { API_V1, PUBLIC_TENANT } from "$lib/utils/config.server.js";
import { codigoError } from "$lib/utils/apiError.js";
import { normalizarContacto } from "$lib/utils/contactoEntidad.js";

const SIN_CACHE = { "Cache-Control": "no-store" };

const NO_DISPONIBLE =
  "La verificación automática no está disponible en este momento. Puede presentar su documento " +
  "de forma presencial en la ventanilla de la entidad o por el correo de atención de la entidad.";

/** @type {import('./$types').RequestHandler} */
export async function GET({ fetch }) {
  if (!PUBLIC_TENANT) {
    return json(
      {
        error: "ventanilla_no_habilitada",
        detail: "Esta entidad no tiene habilitada la radicación en línea.",
      },
      { status: 503, headers: SIN_CACHE },
    );
  }
  try {
    const res = await fetch(
      `${API_V1}/public/${encodeURIComponent(PUBLIC_TENANT)}/ventanilla/reto`,
      { headers: { Accept: "application/json" } },
    );
    const datos = await res.json().catch(() => null);
    if (res.ok && datos && typeof datos === "object") {
      // Allowlist: el widget solo necesita estos cinco campos.
      const { algorithm, challenge, maxnumber, salt, signature } = datos;
      return json({ algorithm, challenge, maxnumber, salt, signature }, { headers: SIN_CACHE });
    }
    if (res.status === 503 || res.status === 404) {
      return json(
        {
          error: "antirrobots_no_disponible",
          detail: NO_DISPONIBLE,
          contacto: normalizarContacto(datos?.contacto ?? datos?.detail?.contacto),
        },
        { status: 503, headers: SIN_CACHE },
      );
    }
    return json(
      {
        error: codigoError(datos) ?? "upstream_error",
        detail: "No se pudo obtener la verificación automática.",
      },
      { status: res.status >= 400 ? res.status : 502, headers: SIN_CACHE },
    );
  } catch {
    return json(
      { error: "antirrobots_no_disponible", detail: NO_DISPONIBLE },
      { status: 503, headers: SIN_CACHE },
    );
  }
}
