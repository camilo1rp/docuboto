// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /firmas/api/certificado/[firmaId] — Proxy SSR para los metadatos de una
 * firma concreta (E06, ADR-016, D-05) — alimenta el Drawer "Certificado de
 * firma".
 *
 * GET → GET /api/v1/signature/firmas/{firma_id}
 *   → FirmaResponse: quién firmó, cuándo, con qué proveedor/nivel XAdES, y
 *     `acreditado` (SIEMPRE false en este incremento — art. 7, no art. 28,
 *     Ley 527/1999).
 *
 * Sin datos sensibles: el backend nunca expone la clave/certificado privado,
 * solo la huella pública (`certificado_fingerprint`). Este proxy no añade ni
 * quita campos — los reenvía tal cual.
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
  const tenant = locals.user?.tenant_slug ?? "";

  const { firmaId } = params;
  if (!firmaId || !UUID_RE.test(firmaId)) {
    return json(
      { error: "validation_error", detail: "El identificador de la firma no es válido." },
      { status: 400 },
    );
  }

  try {
    const res = await fetch(`${API_V1}/signature/firmas/${encodeURIComponent(firmaId)}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
    });

    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo conectar al servidor de firmas." },
      { status: 503 },
    );
  }
}
