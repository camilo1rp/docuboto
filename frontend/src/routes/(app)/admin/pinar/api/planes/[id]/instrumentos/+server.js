// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Articulación con otros instrumentos archivísticos **por referencia, no por
 * copia** (invariante 5 del servicio): el PINAR apunta al CCD/TRD/FUID que ya
 * existen en vez de duplicarlos, que es lo que los dejaría desincronizados.
 */
import { mutacion, UUID_RE } from "$lib/server/pinarProxy.server.js";

const TIPOS = new Set(["ccd", "trd", "fuid", "plan_preservacion", "plan_contingencia"]);

export const POST = mutacion({
  method: "POST",
  path: (p) => (UUID_RE.test(p.id ?? "") ? `/planes/${p.id}/instrumentos` : null),
  campos: ["tipo", "ref", "descripcion"],
  validar: (c) => {
    if (!TIPOS.has(String(c.tipo))) return "Tipo de instrumento inválido.";
    return typeof c.ref === "string" && c.ref.trim() ? null : "Indica la referencia.";
  },
});
