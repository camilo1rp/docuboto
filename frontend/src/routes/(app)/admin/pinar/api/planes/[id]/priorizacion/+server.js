// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * La MATRIZ de priorización: aspecto × eje, impacto 1..10 (Ac. AGN 003/2015,
 * Manual de formulación del PINAR §3).
 *
 * Es un `PUT` y **reemplaza por completo** la priorización de ese aspecto: una
 * matriz a medias no es una priorización, es un cálculo con huecos. La prioridad
 * la deriva el backend (Σ de impactos) y **no se acepta del cliente**: si
 * viajara, el orden de lo que la entidad va a atender primero lo decidiría quien
 * manda el formulario.
 */
import { mutacion, UUID_RE } from "$lib/server/pinarProxy.server.js";

export const PUT = mutacion({
  method: "PUT",
  path: (p) => (UUID_RE.test(p.id ?? "") ? `/planes/${p.id}/priorizacion` : null),
  campos: ["aspecto_id", "celdas"],
  validar: (c) => {
    if (!UUID_RE.test(String(c.aspecto_id ?? ""))) return "Aspecto inválido.";
    if (!Array.isArray(c.celdas) || c.celdas.length === 0) return "Falta la matriz de impactos.";
    for (const celda of c.celdas) {
      const eje = Number(celda?.eje_code);
      const impacto = Number(celda?.impacto);
      if (!Number.isInteger(eje) || eje < 1 || eje > 5) return "Eje fuera de rango.";
      if (!Number.isInteger(impacto) || impacto < 1 || impacto > 10) {
        return "El impacto va de 1 a 10.";
      }
    }
    return null;
  },
});
