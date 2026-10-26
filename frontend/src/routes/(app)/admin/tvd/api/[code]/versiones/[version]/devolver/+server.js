// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/tvd/api/[code]/versiones/[version]/devolver — Proxy SSR (D-05).
 * Fábrica compartida (ADR-026): ver `$lib/server/trdCircuitoProxy.server.js`.
 */
import { makeDevolverHandler } from "$lib/server/trdCircuitoProxy.server.js";

export const POST = makeDevolverHandler("tvd");
