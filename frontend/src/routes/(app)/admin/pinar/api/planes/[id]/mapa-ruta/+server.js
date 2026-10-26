// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/pinar/api/planes/[id]/mapa-ruta — Mapa de ruta del plan (AREA-12-03).
 *
 * GET → `GET /pinar/planes/{id}/mapa-ruta`: los proyectos del plan en el orden
 * en que se ejecutan. Consulta; el backend decide qué ve quien llama.
 */
import { lectura, UUID_RE } from "$lib/server/pinarProxy.server.js";

export const GET = lectura({
  path: (p) => (UUID_RE.test(p.id ?? "") ? `/planes/${p.id}/mapa-ruta` : null),
});
