// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/** Editar o quitar un aspecto crítico. Solo en `borrador` (409 si no). */
import { mutacion, UUID_RE } from "$lib/server/pinarProxy.server.js";

const ruta = (p) => (UUID_RE.test(p.aspectoId ?? "") ? `/aspectos/${p.aspectoId}` : null);

export const PATCH = mutacion({
  method: "PATCH",
  path: ruta,
  campos: ["descripcion", "riesgo", "orden"],
});

export const DELETE = mutacion({ method: "DELETE", path: ruta });
