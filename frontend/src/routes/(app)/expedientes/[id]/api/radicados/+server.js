// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /expedientes/[id]/api/radicados — Proxy SSR: vincular UN radicado al
 * expediente (D-05, ADR-011, Fase 2 cierre desfase API↔UI — la capacidad
 * central del módulo: sin esto un expediente no puede agrupar radicados).
 *
 * POST /api/v1/expedientes/{id}/radicados — `RadicadoLink`
 * (archive-service, `app/schemas/archive.py`): `radicado_id` (uuid) y
 * `tracking_number` (1..50) son obligatorios; el resto de campos del schema
 * (formato, tamano_bytes, folios, dependencia_productora_*, tipologia,
 * content_hash) este proxy NO los pide, y no hay que pedirlos: archive-service
 * resuelve **en el servidor** número, tipología, dependencia productora,
 * formato, tamaño y huella (`ArchiveService._resolver_metadatos_vinculo`,
 * AREA-07-02/06-08) a partir de document-service y storage-service, y coteja
 * `tracking_number` con el que guarda el radicado (422 `metadato_no_coincide`
 * si no es el suyo). Un dato probatorio que aporta quien vincula no prueba nada
 * sobre lo que el archivo custodia, y el índice se firma. Si document-service no
 * responde, el vínculo falla cerrado (503): no se vincula lo que no se pudo
 * verificar.
 *
 * POST → vincula el radicado.
 *   Body: { radicado_id: string (uuid), tracking_number: string }
 *   Respuesta: ExpedienteRadicadoResponse o { error, detail, status }
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, params, request }) {
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

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  const {
    radicado_id: radicadoId,
    tracking_number: trackingNumber,
    tipologia: tipologiaRaw,
  } = body ?? {};

  if (!radicadoId || typeof radicadoId !== "string" || !UUID_RE.test(radicadoId)) {
    return json(
      { error: "validation_error", detail: "Identificador de radicado inválido." },
      { status: 400 },
    );
  }
  if (
    !trackingNumber ||
    typeof trackingNumber !== "string" ||
    !trackingNumber.trim() ||
    trackingNumber.length > 50
  ) {
    return json(
      { error: "validation_error", detail: "Número de radicado inválido." },
      { status: 400 },
    );
  }

  // Allowlist: solo estos campos, nunca el body íntegro (anti mass-assignment).
  // `tipologia` (E15 perfil v4) viaja porque archive-service NO lee la tabla de
  // radicados: si no la aporta quien vincula, el <tipologia> del índice
  // electrónico se emite vacío — y como el XSD lo tipa `xs:string`, valida
  // igual sin decir de qué clase es ningún documento.
  const tipologia =
    typeof tipologiaRaw === "string" && tipologiaRaw.trim() && tipologiaRaw.length <= 100
      ? tipologiaRaw.trim()
      : null;
  const payload = {
    radicado_id: radicadoId,
    tracking_number: trackingNumber.trim(),
    ...(tipologia ? { tipologia } : {}),
  };

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/expedientes/${encodeURIComponent(id)}/radicados`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });

    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo vincular el radicado." },
      { status: 503 },
    );
  }
}
