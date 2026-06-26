// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /expedientes/[id]/api/download/[fileId] — URL pre-firmada de descarga de anexo (D-05, ADR-011).
 *
 * El token se lee de event.locals (cookie httpOnly). El JS del cliente nunca lo ve.
 * La URL pre-firmada devuelta es time-limited y segura de exponer al cliente (no contiene token).
 *
 * GET → obtiene la URL de descarga pre-firmada del storage-service.
 *   Respuesta: { url: string, ... } — la URL es pública y firmada por MinIO, no contiene el token.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.js";

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const { fileId } = params;

  try {
    const res = await fetch(`${API_V1}/storage/files/${fileId}/download`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
    });

    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo obtener el enlace de descarga." },
      { status: 503 },
    );
  }
}
