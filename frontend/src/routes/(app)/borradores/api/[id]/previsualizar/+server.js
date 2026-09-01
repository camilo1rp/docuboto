// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /borradores/api/[id]/previsualizar — el documento tal como saldrá (E23, RF-PRO-05).
 *
 * POST → POST /api/v1/borradores/{id}/previsualizar
 *
 * Devuelve el PDF en crudo, no un sobre JSON: es lo que el visor del navegador
 * necesita. Los campos sin resolver viajan en `X-Faltantes`, que se REEXPONE
 * aquí — sin ella la pantalla enseñaría un documento con huecos y nadie sabría
 * que son huecos.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";

  try {
    const res = await fetch(`${API_V1}/borradores/${encodeURIComponent(params.id)}/previsualizar`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "X-Tenant-Slug": tenant },
    });

    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      return json(data, { status: res.status });
    }

    const pdf = await res.arrayBuffer();
    return new Response(pdf, {
      status: 200,
      headers: {
        "Content-Type": "application/pdf",
        "Content-Disposition": 'inline; filename="previsualizacion.pdf"',
        "X-Faltantes": res.headers.get("X-Faltantes") ?? "",
      },
    });
  } catch {
    return json(
      { error: "gateway_unreachable", detail: "No se pudo contactar el servicio." },
      { status: 502 },
    );
  }
}
