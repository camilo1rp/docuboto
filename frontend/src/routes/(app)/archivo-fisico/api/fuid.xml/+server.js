// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /archivo-fisico/api/fuid.xml — Proxy SSR descarga FUID en XML (D-05, E17).
 *
 * GET → params sanitizados (ubicacion_id, trd_serie_id — UUID format o vacío),
 *       reenvía a GET /fuid.xml?...
 *   Si res.ok: blob pass-through con Content-Type application/xml y
 *              Content-Disposition: attachment; filename="fuid.xml"
 *   Si !res.ok: json(body, {status}) — NUNCA devolver blob de error
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function GET({ locals, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";

  const params = new URLSearchParams();
  const ubicacionId = url.searchParams.get("ubicacion_id") ?? "";
  const trdSerieId = url.searchParams.get("trd_serie_id") ?? "";

  if (ubicacionId && UUID_RE.test(ubicacionId)) params.set("ubicacion_id", ubicacionId);
  if (trdSerieId && UUID_RE.test(trdSerieId)) params.set("trd_serie_id", trdSerieId);

  const qs = params.toString();

  let res;
  try {
    res = await fetch(`${API_V1}/fuid.xml${qs ? `?${qs}` : ""}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
    });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo descargar el FUID XML." },
      { status: 503 },
    );
  }

  if (!res.ok) {
    // NUNCA devolver blob de error — el cliente parsea JSON para mostrar notificación
    const body = await res.json().catch(() => ({
      error: "upstream_error",
      detail: `HTTP ${res.status}`,
    }));
    return json(body, { status: res.status });
  }

  // Pass-through del blob XML con headers de descarga
  const blob = await res.blob();
  return new Response(blob, {
    status: 200,
    headers: {
      "Content-Type": "application/xml; charset=utf-8",
      "Content-Disposition": 'attachment; filename="fuid.xml"',
    },
  });
}
