// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Editar o quitar un proyecto. Solo en `borrador`.
 *
 * `estado` SÍ viaja aquí —el schema `ProyectoUpdate` lo admite y hay estados
 * que no derivan del avance, como `cancelado`— pero `avance_pct` NO: ese lo
 * mueve el seguimiento, y aceptarlo por aquí dejaría declarar un avance sin el
 * registro que lo respalda.
 */
import { mutacion, UUID_RE } from "$lib/server/pinarProxy.server.js";

const ESTADOS = new Set(["planeado", "en_curso", "terminado", "cancelado"]);
const ruta = (p) => (UUID_RE.test(p.proyectoId ?? "") ? `/proyectos/${p.proyectoId}` : null);

export const PATCH = mutacion({
  method: "PATCH",
  path: ruta,
  campos: [
    "objetivo_id",
    "nombre",
    "meta",
    "indicador",
    "responsable",
    "recurso",
    "fecha_inicio",
    "fecha_fin",
    "estado",
  ],
  validar: (c) =>
    c.estado != null && !ESTADOS.has(String(c.estado)) ? "Estado de proyecto inválido." : null,
});

export const DELETE = mutacion({ method: "DELETE", path: ruta });
