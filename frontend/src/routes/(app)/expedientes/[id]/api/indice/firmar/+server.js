// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /expedientes/[id]/api/indice/firmar — Proxy SSR: reintenta el sellado
 * XAdES-B del índice electrónico (D-05, ADR-011, Fase 2, E06 Inc.1, RT-16).
 *
 * POST /api/v1/expedientes/{id}/indice/firmar — exige `USUA_PERM_EXPEDIENTE`
 * Y `PERM_FIRMA` en el backend (archive-service, `routers/indice.py`).
 * Idempotente: el backend responde 409 si el índice vigente ya está firmado
 * (inmutable); 503 si no hay `X-User-Id` resoluble (atribución del acto de
 * firma, RF-FIR-11/12) — este proxy siempre inyecta X-User-Id desde
 * `locals.user.sub`.
 *
 * Acto con efectos jurídicos: la UI que consume este proxy debe pedir
 * confirmación explícita antes de invocarlo (mismo criterio que
 * cerrar/transferir expediente).
 *
 * POST → firma (o reintenta firmar) el índice vigente.
 *   Respuesta: IndiceFirmarResult o { error, detail, status }
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, params }) {
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
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/expedientes/${encodeURIComponent(id)}/indice/firmar`, {
      method: "POST",
      headers,
    });

    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo firmar el índice." },
      { status: 503 },
    );
  }
}
