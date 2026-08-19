// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /archivo-fisico/api/expedientes/[id]/unidades — Proxy SSR localización de expediente (D-05, E17).
 *
 * GET → reenvía a GET /expedientes/{id}/unidades
 *   Permite localizar en qué unidades de conservación se encuentra un expediente.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";
import { resolverExpediente } from "$lib/server/expedienteRef.js";

export async function GET({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  // `id` puede ser el UUID o el CÓDIGO del expediente — es lo que la persona
  // tiene delante y lo único que la interfaz muestra.
  const id = await resolverExpediente(params.id, token, tenant);
  if (!id) {
    return json(
      {
        error: "expediente_no_encontrado",
        detail:
          "No se encontró un expediente con ese número. Revisa el código (ej. EXP-2026-0020).",
      },
      { status: 404 },
    );
  }

  try {
    const res = await fetch(`${API_V1}/expedientes/${id}/unidades`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo localizar el expediente." },
      { status: 503 },
    );
  }
}
