// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /admin/trd/api/cuadro — Proxy SSR: cuadro de clasificación derivado del
 * organigrama y de la TRD vigente (C-4, ADR-040).
 *
 * GET ?incluir_borradores=&incluir_vacias=&segun=convalidado|vivo
 *   → GET /api/v1/trd/cuadro. Solo se reenvían booleanos exactos y `segun`
 *   de la lista cerrada. `incluir_borradores=true` exige USUA_PERM_TRD en el
 *   backend (403 sin él): el 403 se propaga tal cual, la UI solo ofrece el
 *   interruptor a quien tiene el permiso.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";
import { headersFor } from "$lib/server/trdCircuitoProxy.server.js";

const SEGUN = new Set(["convalidado", "vivo"]);

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, url }) {
  if (!locals.accessToken) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  const params = new URLSearchParams();
  for (const clave of ["incluir_borradores", "incluir_vacias"]) {
    const v = url.searchParams.get(clave);
    if (v === "true" || v === "false") params.set(clave, v);
  }
  const segun = url.searchParams.get("segun");
  if (segun && SEGUN.has(segun)) params.set("segun", segun);

  try {
    const res = await fetch(`${API_V1}/trd/cuadro?${params}`, { headers: headersFor(locals) });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo consultar el cuadro de clasificación." },
      { status: 503 },
    );
  }
}
