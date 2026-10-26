// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/** Quitar una referencia a instrumento. Solo en `borrador`. */
import { mutacion, UUID_RE } from "$lib/server/pinarProxy.server.js";

export const DELETE = mutacion({
  method: "DELETE",
  path: (p) =>
    UUID_RE.test(p.id ?? "") && UUID_RE.test(p.instrumentoId ?? "")
      ? `/planes/${p.id}/instrumentos/${p.instrumentoId}`
      : null,
});
