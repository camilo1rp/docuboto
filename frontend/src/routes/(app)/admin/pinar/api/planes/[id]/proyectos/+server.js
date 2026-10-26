// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Proyectos del plan — las «fichas» del mapa de ruta (Ac. AGN 003/2015 §4).
 *
 * `estado` y `avance_pct` **no están en la allowlist de creación**: un proyecto
 * nace `planeado` con avance 0, y el avance se mueve **solo por seguimiento**
 * (append-only). Admitirlos aquí permitiría declarar un proyecto terminado sin
 * un solo registro de avance que lo respalde.
 */
import { mutacion, UUID_RE } from "$lib/server/pinarProxy.server.js";

const CAMPOS = [
  "objetivo_id",
  "nombre",
  "meta",
  "indicador",
  "responsable",
  "recurso",
  "fecha_inicio",
  "fecha_fin",
];

export const POST = mutacion({
  method: "POST",
  path: (p) => (UUID_RE.test(p.id ?? "") ? `/planes/${p.id}/proyectos` : null),
  campos: CAMPOS,
  validar: (c) => {
    if (typeof c.nombre !== "string" || !c.nombre.trim()) return "El proyecto necesita un nombre.";
    if (c.objetivo_id != null && !UUID_RE.test(String(c.objetivo_id))) return "Objetivo inválido.";
    if (c.fecha_inicio && c.fecha_fin && String(c.fecha_fin) < String(c.fecha_inicio)) {
      return "La fecha de fin no puede ser anterior a la de inicio.";
    }
    return null;
  },
});
