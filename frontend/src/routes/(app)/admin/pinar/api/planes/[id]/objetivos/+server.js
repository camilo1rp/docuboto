// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/** Objetivos del plan. Solo en `borrador`. */
import { mutacion, UUID_RE } from "$lib/server/pinarProxy.server.js";

export const POST = mutacion({
  method: "POST",
  path: (p) => (UUID_RE.test(p.id ?? "") ? `/planes/${p.id}/objetivos` : null),
  campos: ["descripcion", "orden"],
  validar: (c) =>
    typeof c.descripcion === "string" && c.descripcion.trim() ? null : "Describe el objetivo.",
});
