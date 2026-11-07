// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * /radicar/api/ingesta/extract — Proxy SSR a POST /knowledge/ingesta/extract.
 */

import { json } from "@sveltejs/kit";
import { API_V1 } from "$lib/utils/config.server.js";

const REF_RE = /^[A-Za-z0-9._:~-]{1,256}$/;
const KINDS = new Set(["choice", "text", "number", "date"]);

/**
 * Plazo de esta llamada al servicio, en ms.
 *
 * Queda por ENCIMA del plazo del proveedor (`PLAZO_EXTRACT_S` = 90 s en
 * `knowledge/clients/ppi.py`) a propósito: así el primer error en llegar es el
 * del proveedor —con su código y su mensaje— y no un corte de este proxy que
 * la pantalla no puede distinguir de una caída de red. El presupuesto de
 * reintentos y el plazo total viven en el cliente (`extractFields.js`).
 */
const PLAZO_MS = 95_000;

/** @type {import('./$types').RequestHandler} */
export async function POST({ locals, request }) {
  const token = locals.accessToken ?? "";
  if (!token) {
    return json({ error: "unauthenticated", detail: "Sesión no válida." }, { status: 401 });
  }
  let body;
  try {
    body = await request.json();
  } catch {
    return json({ error: "invalid_body", detail: "Cuerpo de petición inválido." }, { status: 400 });
  }
  const externalRef = typeof body?.external_ref === "string" ? body.external_ref.trim() : "";
  if (!REF_RE.test(externalRef)) {
    return json(
      { error: "validation_error", detail: "external_ref no tiene la forma acordada." },
      { status: 422 },
    );
  }
  const fields = _fields(body?.fields);
  if (fields === null) {
    return json(
      {
        error: "validation_error",
        detail: "fields debe ser una lista de campos con opciones cerradas.",
      },
      { status: 422 },
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
    const res = await fetch(`${API_V1}/knowledge/ingesta/extract`, {
      method: "POST",
      headers,
      body: JSON.stringify({ external_ref: externalRef, fields }),
      ...(typeof AbortSignal.timeout === "function"
        ? { signal: AbortSignal.timeout(PLAZO_MS) }
        : {}),
    });
    const data = await res.json().catch(() => ({}));
    return json(data, { status: res.status });
  } catch (err) {
    // Un plazo agotado y una red caída son fallos distintos para quien espera:
    // el primero se reintenta a mano, el segundo suele ser momentáneo. Se
    // distinguen por nombre porque `AbortSignal.timeout` lanza `TimeoutError`.
    const agotado = err instanceof Error && err.name === "TimeoutError";
    if (agotado) {
      return json(
        {
          error: "extract_timeout",
          detail: "La extracción tardó demasiado. Vuelve a extraer o llena el formulario.",
          status: 504,
        },
        { status: 504 },
      );
    }
    return json(
      { error: "connection_error", detail: "No se pudo conectar con el servicio de conocimiento." },
      { status: 503 },
    );
  }
}

/**
 * @param {unknown} raw
 * @returns {object[] | null}
 */
function _fields(raw) {
  if (!Array.isArray(raw) || raw.length < 1 || raw.length > 100) {
    return null;
  }
  const fields = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") {
      return null;
    }
    const key = typeof item.key === "string" ? item.key.trim() : "";
    const kind = typeof item.kind === "string" ? item.kind : "";
    if (!key || key.length > 128 || !KINDS.has(kind)) {
      return null;
    }
    /** @type {Record<string, unknown>} */
    const field = { key, kind };
    if (typeof item.label === "string" && item.label.trim()) {
      field.label = item.label.trim().slice(0, 256);
    }
    if (kind === "choice") {
      const choices = _choices(item.choices);
      if (choices === null) {
        return null;
      }
      field.choices = choices;
    }
    fields.push(field);
  }
  return fields;
}

/**
 * @param {unknown} raw
 * @returns {{ value: string, label: string }[] | null}
 */
function _choices(raw) {
  if (!Array.isArray(raw) || raw.length < 1 || raw.length > 500) {
    return null;
  }
  const choices = [];
  for (const item of raw) {
    if (!item || typeof item !== "object") {
      return null;
    }
    const value = typeof item.value === "string" ? item.value.trim() : "";
    const label = typeof item.label === "string" ? item.label.trim() : "";
    if (!value || !label) {
      return null;
    }
    choices.push({ value: value.slice(0, 256), label: label.slice(0, 256) });
  }
  return choices;
}
