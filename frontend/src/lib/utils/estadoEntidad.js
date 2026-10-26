// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * estadoEntidad.js — Estado operativo de la entidad (ADR-035).
 *
 * El api-gateway rechaza toda petición autenticada de una entidad que no está
 * operativa, y lo dice con un código propio:
 *   403 tenant_inactivo              suspendida o inactiva (o inexistente)
 *   503 tenant_no_provisionado       aún sin schema / aprovisionamiento a medias
 *   503 tenant_estado_no_disponible  no se pudo comprobar (fail-closed)
 *
 * Sin distinguirlos, la pantalla lo leía como «me quitaron los permisos» (menú
 * vacío). Aquí se clasifican en tres estados con texto propio.
 */
import { codigoError } from "$lib/utils/apiError.js";

/** @typedef {"suspendida"|"no_lista"|"sin_comprobar"} EstadoEntidad */

/** @type {Record<EstadoEntidad, {titulo: string, cuerpo: string, reintentable: boolean}>} */
export const TEXTOS_ENTIDAD = {
  suspendida: {
    titulo: "La entidad está suspendida",
    cuerpo:
      "El acceso de tu entidad a OrpycaMCP está suspendido. Contacta al administrador de tu entidad; la reactivación la hace el operador de la plataforma.",
    reintentable: false,
  },
  no_lista: {
    titulo: "La entidad aún no está lista",
    cuerpo:
      "La configuración de tu entidad no ha terminado. Vuelve a intentarlo más tarde o contacta al administrador.",
    reintentable: true,
  },
  sin_comprobar: {
    titulo: "No se pudo comprobar el estado de la entidad",
    cuerpo: "Intenta de nuevo en unos segundos. Tu sesión sigue abierta.",
    reintentable: true,
  },
};

/**
 * @param {number} status
 * @param {any} cuerpo cuerpo JSON ya leído (o null)
 * @returns {EstadoEntidad|null} null si no es un rechazo por estado de la entidad
 */
export function clasificarEstadoEntidad(status, cuerpo) {
  const codigo = codigoError(cuerpo);
  if (status === 403 && codigo === "tenant_inactivo") return "suspendida";
  if (status === 503 && codigo === "tenant_no_provisionado") return "no_lista";
  if (status === 503 && codigo === "tenant_estado_no_disponible") return "sin_comprobar";
  return null;
}
