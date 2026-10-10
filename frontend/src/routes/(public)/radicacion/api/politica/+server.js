// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /radicacion/api/politica — política vigente de la entidad, sin sesión.
 * Se usa al recibir 409 `politica_desactualizada`: recarga la política para que
 * quien radica vea la versión nueva y vuelva a autorizar.
 */

import { json } from "@sveltejs/kit";
import { PUBLIC_TENANT } from "$lib/utils/config.server.js";
import { cargarPolitica } from "$lib/server/politicaVentanilla.server.js";

/** @type {import('./$types').RequestHandler} */
export async function GET({ fetch }) {
  if (!PUBLIC_TENANT) return json({ publicada: false, verificable: true }, { status: 503 });
  return json(await cargarPolitica(fetch));
}
