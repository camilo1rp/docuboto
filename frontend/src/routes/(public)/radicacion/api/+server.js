// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /radicacion/api — ventanilla única en línea (sin cuenta).
 *
 * **Sin sesión a propósito**: exigir registrarse para presentar un derecho de
 * petición sería poner una barrera donde la ley no la pone (Ley 1755/2015).
 * La ventanilla de mostrador sigue existiendo en `/radicar`, que sí es de
 * usuario del sistema: son dos puertas al mismo trámite, no alternativas.
 *
 * El multipart se reenvía **tal cual llega** al gateway: los límites de número,
 * tamaño y formato los impone el backend, que es quien manda. Repetirlos aquí
 * daría un aviso más temprano al ciudadano, pero también dos listas que pueden
 * divergir — y la que se puede saltar sería la única que él ve.
 */

import { json } from "@sveltejs/kit";
import { API_V1, PUBLIC_TENANT } from "$lib/utils/config.server.js";
import { codigoError, mensajeError } from "$lib/utils/apiError.js";
import { normalizarContacto } from "$lib/utils/contactoEntidad.js";

/** @type {import('./$types').RequestHandler} */
export async function POST({ request, fetch }) {
  if (!PUBLIC_TENANT) {
    return json(
      {
        error: "ventanilla_no_habilitada",
        detail: "Esta entidad no tiene habilitada la radicación en línea.",
      },
      { status: 503 },
    );
  }

  let cuerpo;
  try {
    cuerpo = await request.formData();
  } catch {
    return json(
      { error: "formato_invalido", detail: "No se pudo leer el formulario." },
      { status: 400 },
    );
  }

  // `Idempotency-Key` (AREA-02-07): la genera el navegador por envío y el backend la
  // ata al radicado, de modo que un reintento de red devuelve el MISMO número en vez de
  // gastar otro consecutivo. Se reenvía solo si viene; el multipart sigue intacto.
  const clave = request.headers?.get?.("idempotency-key");

  try {
    const res = await fetch(`${API_V1}/public/${encodeURIComponent(PUBLIC_TENANT)}/ventanilla`, {
      method: "POST",
      body: cuerpo,
      ...(clave ? { headers: { "Idempotency-Key": clave } } : {}),
    });
    const datos = await res.json().catch(() => null);
    if (res.status === 404) {
      // Entidad sin ventanilla (inexistente, dada de baja o sin aprovisionar): un
      // solo texto, sin distinguir cuál (ADR-035).
      return json(
        { error: "not_found", detail: "Esta entidad no tiene ventanilla disponible." },
        { status: 404 },
      );
    }
    if (res.status === 503 && codigoError(datos) === "canal_no_disponible") {
      // Entidad suspendida: el portal queda en solo lectura (ADR-035); las
      // escrituras públicas dan un mensaje claro, no «No encontrado».
      return json(
        {
          error: "canal_no_disponible",
          detail:
            "Esta entidad tiene suspendida temporalmente la recepción de comunicaciones. Las consultas y verificaciones siguen disponibles.",
        },
        { status: 503 },
      );
    }
    if (!res.ok) {
      return json(
        {
          error: codigoError(datos) ?? "upstream_error",
          detail: mensajeError(datos, `Error ${res.status}`),
          // El 503 antirrobots trae el contacto declarado de la entidad (allowlist de claves).
          ...(codigoError(datos) === "antirrobots_no_disponible"
            ? { contacto: normalizarContacto(datos?.contacto ?? datos?.detail?.contacto) }
            : {}),
        },
        { status: res.status },
      );
    }
    return json(datos, { status: 201 });
  } catch {
    return json(
      {
        error: "upstream_unavailable",
        detail: "No se pudo radicar en este momento. Vuelva a intentarlo.",
      },
      { status: 502 },
    );
  }
}

/** Tipos de trámite que la entidad declara recibir — alimenta el formulario. */
export async function GET({ fetch }) {
  if (!PUBLIC_TENANT) return json([], { status: 200 });
  try {
    const res = await fetch(
      `${API_V1}/public/${encodeURIComponent(PUBLIC_TENANT)}/ventanilla/tipos`,
    );
    return json(res.ok ? await res.json() : []);
  } catch {
    return json([]);
  }
}
