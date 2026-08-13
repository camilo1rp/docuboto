// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /bandeja/api/[id]/publicacion — Proxy SSR del acto de publicación de oficio
 * (Ley 1712/2014 art. 12, D-05).
 *
 * Endpoints reales:
 *   GET  /api/v1/documents/{id}              → estado (publicable, publicado_at,
 *                                              nivel_seguridad, tracking_number)
 *   POST /api/v1/documents/{id}/publicacion  → { tracking_number, publicable,
 *                                              publicado_at }
 *
 * Por qué existe esta pantalla: `publicable` arranca en FALSE y solo ese POST
 * lo cambia. Sin este camino, una institución que actualice se queda con todos
 * sus radicados no publicables y **sin forma de publicarlos desde la
 * aplicación** — el esquema de publicación quedaría inejecutable salvo por SQL.
 *
 * El GET **no** consulta un endpoint de publicación propio (no existe): lee el
 * detalle del radicado, que expone el estado. Se devuelve un objeto recortado a
 * los cuatro campos que la pantalla necesita, en lugar de reenviar el
 * `DocumentResponse` completo: el drawer no tiene por qué recibir el asunto ni
 * los anexos para decidir sobre la publicación.
 *
 * No-read-up (RF-SEG-08): un radicado inexistente, uno por encima del clearance
 * del llamante, uno que no es de nivel público y uno anulado devuelven el MISMO
 * 404 neutro en el backend. Este proxy preserva esa opacidad — distinguirlos
 * convertiría la pantalla en un oráculo sobre radicados reservados.
 *
 * Allowlist campo a campo del `PublicacionRequest` real (schemas/document.py):
 * `publicar` (bool) y `fundamento` (str, 1..500). El fundamento es obligatorio
 * en AMBOS sentidos: retirar una publicación también hay que poder explicarlo.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const FUNDAMENTO_MAX = 500;

function opaque404() {
  return json(
    {
      error: "not_found",
      detail:
        "No fue posible encontrar el radicado, no tiene acceso a él, " +
        "o no admite el acto de publicación.",
    },
    { status: 404 },
  );
}

/**
 * Cabeceras de identidad derivadas SIEMPRE de `locals` (nunca del body): D-05.
 * @param {App.Locals} locals
 * @param {boolean} withBody
 */
function authHeaders(locals, withBody) {
  const token = locals.accessToken ?? "";
  if (!token) return null;
  /** @type {Record<string, string>} */
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": locals.user?.tenant_slug ?? "",
  };
  const userId = locals.user?.sub ?? "";
  if (userId) headers["X-User-Id"] = userId;
  if (withBody) headers["Content-Type"] = "application/json";
  return headers;
}

function unauthenticated() {
  return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
}

function connectionError() {
  return json(
    { error: "connection_error", detail: "No se pudo conectar al servidor de documentos." },
    { status: 503 },
  );
}

/** @type {import('./$types').RequestHandler} */
export async function GET({ locals, params }) {
  const headers = authHeaders(locals, false);
  if (!headers) return unauthenticated();

  try {
    const res = await fetch(`${API_V1}/documents/${encodeURIComponent(params.id)}`, { headers });
    if (res.status === 404) return opaque404();
    const data = await res.json().catch(() => ({}));
    if (!res.ok) return json(data, { status: res.status });
    return json(
      {
        tracking_number: data.tracking_number ?? "",
        nivel_seguridad: data.nivel_seguridad ?? null,
        status: data.status ?? "",
        publicable: Boolean(data.publicable),
        publicado_at: data.publicado_at ?? null,
      },
      { status: 200 },
    );
  } catch {
    return connectionError();
  }
}

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, params, request }) {
  const headers = authHeaders(locals, true);
  if (!headers) return unauthenticated();

  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }

  const { publicar, fundamento } = body ?? {};

  if (typeof publicar !== "boolean") {
    return json(
      {
        error: "validation_error",
        detail: "Debe indicarse si el acto publica o retira la publicación.",
      },
      { status: 422 },
    );
  }

  // El fundamento se valida aquí además de en el backend: un campo obligatorio
  // que solo se comprueba al otro lado devuelve un 422 genérico de Pydantic,
  // y quien está redactando un acto administrativo merece saber qué falta.
  const texto = typeof fundamento === "string" ? fundamento.trim() : "";
  if (!texto) {
    return json(
      {
        error: "validation_error",
        detail: "El fundamento del acto de publicación es obligatorio.",
      },
      { status: 422 },
    );
  }
  if (texto.length > FUNDAMENTO_MAX) {
    return json(
      {
        error: "validation_error",
        detail: `El fundamento no puede superar ${FUNDAMENTO_MAX} caracteres.`,
      },
      { status: 422 },
    );
  }

  try {
    const res = await fetch(`${API_V1}/documents/${encodeURIComponent(params.id)}/publicacion`, {
      method: "POST",
      headers,
      body: JSON.stringify({ publicar, fundamento: texto }),
    });
    if (res.status === 404) return opaque404();
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch {
    return connectionError();
  }
}
