// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /firmas/api/verify — Proxy SSR para verificar la integridad de una firma
 * existente (E06, ADR-016, D-05) — usado desde el Drawer de certificado.
 *
 * POST { firma_id, payload_xml }
 *   → POST /api/v1/signature/verify
 *   → VerifyResponse { valida, provider, xades_level, motivo, ... }
 *
 * El backend no exige autenticación en esta operación (es de consulta
 * pública, ver signature.py::verify), pero este proxy SÍ exige sesión — la
 * pantalla completa vive detrás de login; no hay necesidad de abrir un
 * agujero anónimo en el front por una propiedad interna del backend.
 *
 * `payload_xml` es el documento COMPLETO tal como fue firmado (para
 * proveedores XAdES, el XML firmado descargado; para 'nativa', el payload
 * original). Este proxy NO reconstruye ni adivina ese contenido — lo recibe
 * del cliente tal cual (herramienta de verificación técnica, no automática:
 * ver CertificadoDrawer.svelte). Allowlist estricta: solo firma_id/payload_xml.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  const tenant = locals.user?.tenant_slug ?? "";

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  const { firma_id, payload_xml } = body ?? {};

  if (!firma_id || typeof firma_id !== "string" || !UUID_RE.test(firma_id)) {
    return json(
      { error: "validation_error", detail: "firma_id debe ser un UUID válido." },
      { status: 422 },
    );
  }
  if (!payload_xml || typeof payload_xml !== "string" || !payload_xml.trim()) {
    return json(
      { error: "validation_error", detail: "payload_xml es obligatorio." },
      { status: 422 },
    );
  }

  try {
    const res = await fetch(`${API_V1}/signature/verify`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
      body: JSON.stringify({ firma_id, payload_xml }),
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
