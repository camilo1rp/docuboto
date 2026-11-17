// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * (public)/firma — «Comprobar una firma» (ADR-054 §4). Sin sesión.
 *
 * El formulario envía `?codigo=` por GET (funciona sin JavaScript) y aquí se
 * redirige a `/firma/{codigo}`, que es la dirección estable que la entidad puede
 * imprimir junto al documento firmado. La forma se acota antes de salir a la red:
 * el código es el identificador de la firma, un UUID.
 */
import { redirect } from "@sveltejs/kit";
import { esCodigoDeFirma } from "$lib/utils/verificacionFirma.js";

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export function load({ url }) {
  const codigo = (url.searchParams.get("codigo") ?? "").trim();
  if (!codigo) return { codigo: "", mensaje: null };
  if (!esCodigoDeFirma(codigo)) {
    return {
      codigo,
      mensaje:
        "El código de una firma tiene 36 caracteres con guiones, por ejemplo " +
        "3f2c1a9e-4b7d-4e2a-9c1f-0a1b2c3d4e5f. Revíselo.",
    };
  }
  redirect(303, `/firma/${encodeURIComponent(codigo.toLowerCase())}`);
}
