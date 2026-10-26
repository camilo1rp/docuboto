// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /expedientes/[id]/api/indice-xml — Descarga del XML del índice electrónico (D-05, ADR-011).
 *
 * El token se lee de event.locals (cookie httpOnly). El JS del cliente nunca lo ve.
 * Hace pass-through del blob XML del gateway sin cargarlo en memoria completa.
 *
 * GET → exporta el XML del índice electrónico E15.
 *   Parámetros: version (entero, opcional)
 *   Respuesta: application/xml (binario, pass-through del gateway)
 */

import { API_V1 } from "$lib/utils/config.server.js";
import { json } from "@sveltejs/kit";

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, params, url }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";
  const { id } = params;

  // Saneamiento: solo se admite version entera; cualquier otro valor se descarta
  // para evitar inyectar parámetros arbitrarios en el request al gateway.
  const version = url.searchParams.get("version");
  const qs = new URLSearchParams();
  if (version && /^\d+$/.test(version)) qs.set("version", version);
  const versionParam = qs.toString() ? `?${qs}` : "";

  let res;
  try {
    res = await fetch(`${API_V1}/expedientes/${id}/indice.xml${versionParam}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
    });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo descargar el índice XML." },
      { status: 503 },
    );
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({
      error: "upstream_error",
      detail: `HTTP ${res.status}`,
    }));
    return json(body, { status: res.status });
  }

  // Pass-through del blob XML como respuesta de streaming
  const blob = await res.blob();
  return new Response(blob, {
    status: 200,
    headers: {
      "Content-Type": "application/xml; charset=utf-8",
    },
  });
}
