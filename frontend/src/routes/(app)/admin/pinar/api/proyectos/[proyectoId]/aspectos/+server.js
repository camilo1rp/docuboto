// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Qué aspectos críticos atiende el proyecto. Es la **articulación** que hace que
 * el tablero por eje tenga algo que agregar: sin ella, un proyecto avanza y el
 * avance no se atribuye a ningún eje del PINAR.
 */
import { mutacion, UUID_RE } from "$lib/server/pinarProxy.server.js";

export const PUT = mutacion({
  method: "PUT",
  path: (p) => (UUID_RE.test(p.proyectoId ?? "") ? `/proyectos/${p.proyectoId}/aspectos` : null),
  campos: ["aspecto_ids"],
  validar: (c) =>
    Array.isArray(c.aspecto_ids) && c.aspecto_ids.every((i) => UUID_RE.test(String(i)))
      ? null
      : "Aspectos inválidos.",
});
