// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /expedientes/[id]/api/aip — Proxy SSR: paquete de preservación (AIP) del
 * expediente (D-05, ADR-011, Fase 2, E10).
 *
 * GET /api/v1/expedientes/{id}/aip → `AipResponse` (archive-service, media
 * hacia storage-service — único punto de acceso EXTERNO al AIP tras el
 * cierre del no-read-up de E10 Increment C: storage-service ya exige
 * `X-Internal-Token`, así que este proxy JAMÁS podría alcanzarlo por otra
 * vía). 404 neutro si el expediente no es visible o no tiene AIP empaquetado.
 *
 * GET → metadatos del AIP (no el binario — `aip_bytes` es un tamaño, no el
 *   contenido; no hay descarga directa del AIP en el contrato v1).
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, params }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }

  const { id } = params;
  if (!id || !UUID_RE.test(id)) {
    return json(
      { error: "validation_error", detail: "Identificador de expediente inválido." },
      { status: 400 },
    );
  }

  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";

  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/expedientes/${encodeURIComponent(id)}/aip`, { headers });

    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo obtener el paquete de preservación." },
      { status: 503 },
    );
  }
}
