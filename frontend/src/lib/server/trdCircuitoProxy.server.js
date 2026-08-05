// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * trdCircuitoProxy.server.js — Fábricas de proxies SSR (D-05) para el
 * circuito de convalidación de instrumentos archivísticos (aprobar /
 * devolver / registrar-rusd / convalidar / derogar / listar versiones).
 *
 * ADR-026 D2/§"Superficie de API": TRD y TVD comparten el MISMO circuito
 * (Ac. AGN 001/2024, que compila el 004/2019) sobre rutas separadas
 * (`/api/v1/trd/...` y `/api/v1/tvd/...`), con cuerpos de petición
 * IDÉNTICOS entre instrumentos — solo cambia el segmento de la URL. Esta
 * fábrica se parametriza por ese segmento (`"trd"` | `"tvd"`) para que la
 * lógica de validación/allowlist exista UNA sola vez: si algún día
 * divergiera, el diff lo haría visible en la fábrica, no en dos copias que
 * podrían desalinearse en silencio.
 *
 * El contenido PROPIO de cada instrumento (crear serie/agrupación, nueva
 * versión, PATCH) NO vive aquí — esos payloads sí difieren (ADR-026 D2 Capa
 * 3: TVD exige fondo_nombre/fechas extremas/justificación, TRD no) y cada
 * ruta +server.js de admin/trd/api/... y admin/tvd/api/... los declara por
 * su cuenta.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const VERSION_RE = /^[1-9]\d*$/;
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;

/** @param {any} locals @param {boolean} [withContentType] */
export function headersFor(locals, withContentType = false) {
  const tenant = locals.user?.tenant_slug ?? "";
  const userId = locals.user?.sub ?? "";
  /** @type {Record<string, string>} */
  const headers = { Authorization: `Bearer ${locals.accessToken}`, "X-Tenant-Slug": tenant };
  if (userId) headers["X-User-Id"] = userId;
  if (withContentType) headers["Content-Type"] = "application/json";
  return headers;
}

function unauthenticated() {
  return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
}

function badRequest(detail) {
  return json({ error: "validation_error", detail }, { status: 400 });
}

/** @param {Record<string, any>} params */
function validateCode(params) {
  const { code } = params ?? {};
  if (typeof code !== "string" || !code.trim()) {
    return { error: badRequest("Código de instrumento inválido.") };
  }
  return { code: code.trim() };
}

/** @param {Record<string, any>} params */
function validateCodeVersion(params) {
  const v = validateCode(params);
  if (v.error) return v;
  const { version } = params;
  if (!VERSION_RE.test(String(version))) {
    return { error: badRequest("Versión inválida.") };
  }
  return { code: v.code, version: String(version) };
}

/** `GET /{segment}/{code}/versiones` — historia completa (ADR-026, gemelo). */
export function makeVersionesGetHandler(segment) {
  /** @type {import('@sveltejs/kit').RequestHandler} */
  return async function GET({ locals, params }) {
    if (!locals.accessToken) return unauthenticated();
    const v = validateCode(params);
    if (v.error) return v.error;
    try {
      const res = await fetch(`${API_V1}/${segment}/${encodeURIComponent(v.code)}/versiones`, {
        headers: headersFor(locals),
      });
      const data = await res.json().catch(() => []);
      return json(data, { status: res.status });
    } catch {
      return json(
        { error: "connection_error", detail: "No se pudo consultar el historial de versiones." },
        { status: 503 },
      );
    }
  };
}

/**
 * Fábrica genérica de una acción `POST /{segment}/{code}/versiones/{version}/{action}`.
 * @param {string} segment "trd" | "tvd"
 * @param {string} action "aprobar" | "devolver" | "registrar-rusd" | "convalidar" | "derogar"
 * @param {{ buildPayload?: (body: any) => { payload?: object, error?: Response }, fallbackError?: string }} [opts]
 */
function makeActionHandler(segment, action, opts = {}) {
  const { buildPayload, fallbackError } = opts;
  /** @type {import('@sveltejs/kit').RequestHandler} */
  return async function POST({ locals, params, request }) {
    if (!locals.accessToken) return unauthenticated();
    const v = validateCodeVersion(params);
    if (v.error) return v.error;

    let payload = {};
    if (buildPayload) {
      let body;
      try {
        body = await request.json();
      } catch {
        return json(
          { error: "invalid_body", detail: "Cuerpo de petición inválido." },
          { status: 400 },
        );
      }
      const built = buildPayload(body ?? {});
      if (built.error) return built.error;
      payload = built.payload ?? {};
    }

    try {
      const res = await fetch(
        `${API_V1}/${segment}/${encodeURIComponent(v.code)}/versiones/${v.version}/${action}`,
        { method: "POST", headers: headersFor(locals, true), body: JSON.stringify(payload) },
      );
      const data = await res.json().catch(() => ({}));
      return json(data, { status: res.status });
    } catch {
      return json(
        { error: "connection_error", detail: fallbackError ?? "No se pudo completar la acción." },
        { status: 503 },
      );
    }
  };
}

/** `POST .../aprobar` — body { acta_comite, fecha_aprobacion_comite } (TrdSerieAprobar). */
export function makeAprobarHandler(segment) {
  return makeActionHandler(segment, "aprobar", {
    fallbackError: "No se pudo aprobar la versión.",
    buildPayload(body) {
      const { acta_comite, fecha_aprobacion_comite } = body;
      if (typeof acta_comite !== "string" || !acta_comite.trim()) {
        return { error: badRequest("El acta del Comité es obligatoria.") };
      }
      if (typeof fecha_aprobacion_comite !== "string" || !DATE_RE.test(fecha_aprobacion_comite)) {
        return {
          error: badRequest("La fecha de aprobación del Comité es obligatoria (ISO date)."),
        };
      }
      return { payload: { acta_comite: acta_comite.trim(), fecha_aprobacion_comite } };
    },
  });
}

/** `POST .../devolver` — body { motivo } (TrdSerieDevolver). */
export function makeDevolverHandler(segment) {
  return makeActionHandler(segment, "devolver", {
    fallbackError: "No se pudo devolver la versión.",
    buildPayload(body) {
      const { motivo } = body;
      if (typeof motivo !== "string" || !motivo.trim()) {
        return { error: badRequest("El motivo de la devolución es obligatorio.") };
      }
      return { payload: { motivo: motivo.trim() } };
    },
  });
}

/** `POST .../registrar-rusd` — body { rusd_radicado?, fecha_publicacion? }, al menos uno. */
export function makeRegistrarRusdHandler(segment) {
  return makeActionHandler(segment, "registrar-rusd", {
    fallbackError: "No se pudo registrar el RUSD.",
    buildPayload(body) {
      const { rusd_radicado, fecha_publicacion } = body;
      const hasRusd = typeof rusd_radicado === "string" && rusd_radicado.trim();
      const hasFecha = typeof fecha_publicacion === "string" && DATE_RE.test(fecha_publicacion);
      if (!hasRusd && !hasFecha) {
        return {
          error: badRequest(
            "Debe indicar el radicado RUSD o la fecha de publicación (al menos uno).",
          ),
        };
      }
      /** @type {Record<string, unknown>} */
      const payload = {};
      if (hasRusd) payload.rusd_radicado = rusd_radicado.trim();
      if (hasFecha) payload.fecha_publicacion = fecha_publicacion;
      return { payload };
    },
  });
}

/** `POST .../convalidar` — body { acto_administrativo, fecha_convalidacion, instancia_convalidante, rusd_radicado?, fecha_publicacion? }. */
export function makeConvalidarHandler(segment) {
  return makeActionHandler(segment, "convalidar", {
    fallbackError: "No se pudo convalidar la versión.",
    buildPayload(body) {
      const {
        acto_administrativo,
        fecha_convalidacion,
        instancia_convalidante,
        rusd_radicado,
        fecha_publicacion,
      } = body;
      if (typeof acto_administrativo !== "string" || !acto_administrativo.trim()) {
        return { error: badRequest("El acto administrativo es obligatorio.") };
      }
      if (typeof fecha_convalidacion !== "string" || !DATE_RE.test(fecha_convalidacion)) {
        return { error: badRequest("La fecha de convalidación es obligatoria (ISO date).") };
      }
      if (typeof instancia_convalidante !== "string" || !instancia_convalidante.trim()) {
        return { error: badRequest("La instancia convalidante es obligatoria.") };
      }
      /** @type {Record<string, unknown>} */
      const payload = {
        acto_administrativo: acto_administrativo.trim(),
        fecha_convalidacion,
        instancia_convalidante: instancia_convalidante.trim(),
      };
      if (typeof rusd_radicado === "string" && rusd_radicado.trim())
        payload.rusd_radicado = rusd_radicado.trim();
      if (typeof fecha_publicacion === "string" && DATE_RE.test(fecha_publicacion)) {
        payload.fecha_publicacion = fecha_publicacion;
      }
      return { payload };
    },
  });
}

/** `POST .../derogar` — sin body. */
export function makeDerogarHandler(segment) {
  return makeActionHandler(segment, "derogar", { fallbackError: "No se pudo derogar la versión." });
}
