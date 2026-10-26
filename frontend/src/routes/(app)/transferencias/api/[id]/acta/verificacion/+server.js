// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /transferencias/api/[id]/acta/verificacion — Proxy SSR de la verificación
 * agregada (sello institucional + firmas personales) del acta de entrega
 * (D-05, E17/F4) — `GET /transferencias/{id}/acta/verificacion`.
 *
 * SIEMPRE cripto+revocación ON-DEMAND (nunca cacheada, ver
 * `TransferenciaService.acta_verificacion`) — el llamador del cliente decide
 * cuándo pagar el costo (botón "Verificar firmas"), nunca en la carga
 * inicial masiva de la subruta de detalle.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

export async function GET({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const { id } = params;

  try {
    const res = await fetch(
      `${API_V1}/transferencias/${encodeURIComponent(id)}/acta/verificacion`,
      {
        headers: {
          Authorization: `Bearer ${token}`,
          "X-Tenant-Slug": tenant,
        },
      },
    );
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo verificar el acta de entrega." },
      { status: 503 },
    );
  }
}
