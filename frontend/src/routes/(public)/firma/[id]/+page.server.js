// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * (public)/firma/[id] — verificación pública de una firma por su código (ADR-054 §2).
 *
 * Sin sesión. Pasa por el servidor —y no se llama al gateway desde el navegador—
 * para que la URL interna del gateway y el inquilino no viajen al cliente. La
 * respuesta es content-free por construcción (signature-service no devuelve el
 * XML ni el documento); aquí solo se dibuja.
 *
 * Un 404 es NEUTRO: el backend responde lo mismo para un código que no existe y
 * para la firma de un documento clasificado. La pantalla no deshace esa
 * indistinción. Un 503 no se presenta como «firma inválida»: no poder comprobar
 * y comprobar que no vale son afirmaciones distintas.
 */
import { API_V1, PUBLIC_TENANT } from "$lib/utils/config.server.js";
import { esCodigoDeFirma } from "$lib/utils/verificacionFirma.js";

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ params, fetch }) {
  const codigo = (params.id ?? "").trim();
  if (!PUBLIC_TENANT) {
    return {
      codigo,
      disponible: false,
      verificacion: null,
      mensaje: "Esta entidad no tiene habilitada la verificación en línea de firmas.",
    };
  }
  if (!esCodigoDeFirma(codigo)) {
    return {
      codigo,
      disponible: true,
      verificacion: null,
      mensaje: "El código de una firma tiene 36 caracteres con guiones. Revíselo.",
    };
  }
  try {
    const res = await fetch(
      `${API_V1}/public/signature/${encodeURIComponent(PUBLIC_TENANT)}/${encodeURIComponent(codigo)}`,
    );
    if (res.status === 404) {
      return {
        codigo,
        disponible: true,
        verificacion: null,
        mensaje: "No encontramos ninguna firma con ese código. Verifique que lo copió completo.",
      };
    }
    if (!res.ok) {
      return {
        codigo,
        disponible: true,
        verificacion: null,
        mensaje: "No se pudo comprobar la firma en este momento. Intente de nuevo en unos minutos.",
      };
    }
    return { codigo, disponible: true, verificacion: await res.json(), mensaje: null };
  } catch {
    return {
      codigo,
      disponible: true,
      verificacion: null,
      mensaje: "No se pudo conectar con el servicio de firmas.",
    };
  }
}
