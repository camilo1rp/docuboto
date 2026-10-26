// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * envios/api/[id]/tracking — Proxy SSR de "Actualizar rastreo" (D-05, Fase 8).
 *
 * GET /api/v1/envios/{shipment_id}/tracking — consulta PULL contra el
 * operador postal (F5, postal.py). Responde `TrackingResponse`:
 *   {shipment_id, operador, guia, estado_interno_actual, estado_operador,
 *    estado_interno_mapeado, operador_conectado}
 *
 * `operador_conectado=false` indica que no hay integración en vivo con el
 * operador (stub) — el estado mostrado es el último conocido internamente,
 * no una consulta real; la UI debe comunicar esa distinción, no ocultarla.
 */

import { API_V1 } from "$lib/utils/config.server.js";
import { json } from "@sveltejs/kit";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  if (!params.id || !UUID_RE.test(params.id)) {
    return json(
      { error: "validation_error", detail: "Identificador de envío inválido." },
      { status: 400 },
    );
  }
  const tenant = locals.user?.tenant_slug ?? "";

  try {
    const res = await fetch(`${API_V1}/envios/${encodeURIComponent(params.id)}/tracking`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo consultar el rastreo del envío." },
      { status: 503 },
    );
  }
}
