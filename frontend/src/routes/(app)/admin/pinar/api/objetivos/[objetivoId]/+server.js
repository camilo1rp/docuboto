// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/** Editar o quitar un objetivo. Solo en `borrador`. */
import { mutacion, UUID_RE } from "$lib/server/pinarProxy.server.js";

const ruta = (p) => (UUID_RE.test(p.objetivoId ?? "") ? `/objetivos/${p.objetivoId}` : null);

export const PATCH = mutacion({ method: "PATCH", path: ruta, campos: ["descripcion", "orden"] });
export const DELETE = mutacion({ method: "DELETE", path: ruta });
