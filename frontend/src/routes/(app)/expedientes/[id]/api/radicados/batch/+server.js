// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /expedientes/[id]/api/radicados/batch — Proxy SSR: vincular VARIOS
 * radicados de una vez (D-05, ADR-011, Fase 2 — el caso de uso normal: un
 * operador que arma un expediente rara vez vincula de a uno).
 *
 * POST /api/v1/expedientes/{id}/radicados/batch — `BatchLinkRequest`
 * (archive-service, `app/schemas/archive.py`): `radicados: RadicadoLink[]`,
 * 1..500 elementos. Mismo criterio de allowlist que el proxy hermano
 * `radicados/+server.js` (solo `radicado_id`/`tracking_number` por ítem).
 *
 * `BatchLinkResult.vinculados`/`omitidos` pueden venir `null` (M2,
 * RF-SEG-08): la vinculación SIEMPRE se ejecuta, pero si el llamante no
 * puede LEER el expediente el backend acota la respuesta para no revelar el
 * volumen documental — este proxy reenvía la respuesta tal cual, sin
 * rellenar esos `null` con un valor inventado.
 *
 * POST → vincula el lote.
 *   Body: { radicados: Array<{ radicado_id: string, tracking_number: string }> }
 *   Respuesta: BatchLinkResult o { error, detail, status }
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MAX_BATCH = 500;

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

  const radicados = Array.isArray(body?.radicados) ? body.radicados : null;

  if (!radicados || radicados.length === 0) {
    return json(
      { error: "validation_error", detail: "Seleccione al menos un radicado para vincular." },
      { status: 400 },
    );
  }
  if (radicados.length > MAX_BATCH) {
    return json(
      {
        error: "validation_error",
        detail: `No se pueden vincular más de ${MAX_BATCH} radicados en un lote.`,
      },
      { status: 400 },
    );
  }

  /** @type {Array<{radicado_id: string, tracking_number: string}>} */
  const cleaned = [];
  for (const item of radicados) {
    const radicadoId = item?.radicado_id;
    const trackingNumber = item?.tracking_number;
    if (!radicadoId || typeof radicadoId !== "string" || !UUID_RE.test(radicadoId)) {
      return json(
        {
          error: "validation_error",
          detail: "Uno de los radicados seleccionados tiene un id inválido.",
        },
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
        {
          error: "validation_error",
          detail: "Uno de los radicados seleccionados tiene un número de radicado inválido.",
        },
        { status: 400 },
      );
    }
    // Allowlist por ítem (anti mass-assignment). `tipologia` entra por la
    // misma razón que en el proxy hermano: archive-service no lee la tabla de
    // radicados, así que el <tipologia> del índice electrónico solo llega si
    // lo aporta quien vincula (E15 perfil v4, RT-15).
    const tipologiaRaw = item?.tipologia;
    const tipologia =
      typeof tipologiaRaw === "string" && tipologiaRaw.trim() && tipologiaRaw.length <= 100
        ? tipologiaRaw.trim()
        : null;
    cleaned.push({
      radicado_id: radicadoId,
      tracking_number: trackingNumber.trim(),
      ...(tipologia ? { tipologia } : {}),
    });
  }

  const payload = { radicados: cleaned };

  /** @type {Record<string, string>} */
  const headers = {
    "Content-Type": "application/json",
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };
  if (userId) headers["X-User-Id"] = userId;

  try {
    const res = await fetch(`${API_V1}/expedientes/${encodeURIComponent(id)}/radicados/batch`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
    });

    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return json(
      { error: "connection_error", detail: "No se pudo vincular el lote de radicados." },
      { status: 503 },
    );
  }
}
