// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * expedientes/+page.server.js — Listado de expedientes (T-12, RF-UI-05).
 *
 * Endpoint real: GET /api/v1/expedientes/search
 *   - `q` es OPCIONAL y tiene min_length=2 en el backend: si el texto tiene
 *     menos de 2 caracteres tras trim() se OMITE por completo del query string
 *     (enviar `q=` provoca 422). Sin `q`, el endpoint es un listado ordenado
 *     por opened_at DESC — es el contrato elegido por orfeo-architect para
 *     esta vista porque ya filtra por clearance no-read-up (RF-SEG-08) y
 *     proyecta solo 6 campos (ExpedienteSearchHit) en vez de los 13+ de
 *     ExpedienteResponse.
 *   - Filtros soportados: q, status (open|closed|transferred), date_from,
 *     date_to, page, size. Todos se leen de la URL (deep-link friendly) y
 *     se sanean contra una allowlist estricta antes de reenviarlos.
 *   - Paginación vía el campo `total` del body (ExpedienteSearchResponse).
 *     X-Total-Count NO está en FORWARDED_RESPONSE_HEADERS del api-gateway
 *     (services/api-gateway/app/core/proxy.py) — en producción el header
 *     nunca llega y SIEMPRE se usa el fallback `body.total`. Se sigue leyendo
 *     el header por si el gateway empieza a reenviarlo, pero no es la vía
 *     real hoy (M-6).
 *
 * PATRÓN (D-05, coherente con bandeja/busqueda/workspace de expediente):
 *   - Todo el estado de la vista vive en la URL. Los cambios de filtro y de
 *     página usan goto() para actualizar la URL, lo que re-ejecuta este load
 *     (navegación cliente de SvelteKit, sin recarga completa del navegador).
 *   - NO hay ningún +server.js/proxy para esta vista: cada filtro/página es
 *     un ciclo SSR completo. Un proxy solo añadiría superficie de ataque
 *     (D-05) para ahorrar una latencia que aquí no es crítica (listado, no
 *     edición inline).
 *   - El access_token NO se expone en $page.data — nunca alcanza el JS del
 *     cliente.
 *
 * NO se envía X-User-Id: no está en FORWARDED_REQUEST_HEADERS del gateway
 * (services/api-gateway/app/core/proxy.py) y el gateway inyecta el suyo
 * propio desde /validate — el header del cliente es un no-op (D-07/E22 T-12).
 */

import { fail, redirect } from "@sveltejs/kit";
import { COOKIE_ACCESS_TOKEN } from "$lib/services/auth.server.js";
import { API_V1 } from "$lib/utils/config.server.js";

const STATUS_ALLOWLIST = ["open", "closed", "transferred"];
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
// Dígitos puros, 1-5 caracteres (tope 99999): rechaza " 1 ", "0x10", "+5",
// "1e9", decimales, etc. Sin esto, Number("1e9") es finito y >=1, y se
// reenvía page=1000000000 al gateway → OFFSET 19999999980 en el repo (M-2).
const PAGE_RE = /^\d{1,5}$/;
const PAGE_SIZE = 20;
const Q_MAX_LENGTH = 200;

/**
 * Sanea los query params de la URL contra una allowlist estricta.
 * Cualquier valor fuera de la allowlist se descarta silenciosamente (no 422).
 * @param {URL} url
 */
function readFilters(url) {
  const q = (url.searchParams.get("q") ?? "").trim().slice(0, Q_MAX_LENGTH);

  const rawStatus = url.searchParams.get("status") ?? "";
  const status = STATUS_ALLOWLIST.includes(rawStatus) ? rawStatus : "";

  const rawDateFrom = url.searchParams.get("date_from") ?? "";
  const dateFrom = DATE_RE.test(rawDateFrom) ? rawDateFrom : "";

  const rawDateTo = url.searchParams.get("date_to") ?? "";
  const dateTo = DATE_RE.test(rawDateTo) ? rawDateTo : "";

  const rawPage = url.searchParams.get("page") ?? "1";
  const page = PAGE_RE.test(rawPage) ? Math.max(1, Number(rawPage)) : 1;

  return { q, status, dateFrom, dateTo, page };
}

/** @param {import('@sveltejs/kit').ServerLoadEvent} event */
export async function load({ cookies, parent, url, fetch }) {
  const { user } = await parent();

  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";

  const { q, status, dateFrom, dateTo, page } = readFilters(url);
  const initialFilters = { q, status, dateFrom, dateTo, page };

  // Guard (D-05): sin token no se llama al gateway. En condiciones normales
  // (app)/+layout.server.js ya redirige a /login antes de llegar aquí; esto
  // protege el caso límite de que la cookie desaparezca entre ambas cargas.
  if (!token) {
    return {
      expedientes: [],
      total: 0,
      loadError: "Sesión no válida. Vuelve a iniciar sesión.",
      initialFilters,
    };
  }

  const params = new URLSearchParams({ page: String(page), size: String(PAGE_SIZE) });
  // Trampa del contrato: min_length=2 en el backend — omitir `q` por debajo
  // de 2 caracteres. Enviar `q=` o `q=a` da 422.
  if (q.length >= 2) params.set("q", q);
  if (status) params.set("status", status);
  if (dateFrom) params.set("date_from", dateFrom);
  if (dateTo) params.set("date_to", dateTo);

  let expedientes = [];
  let total = 0;
  let loadError = null;

  try {
    const res = await fetch(`${API_V1}/expedientes/search?${params.toString()}`, {
      headers: {
        Authorization: `Bearer ${token}`,
        "X-Tenant-Slug": tenant,
      },
    });

    if (res.ok) {
      const body = await res.json();
      expedientes = body.hits ?? [];
      // El header casi nunca llega (ver nota arriba, M-6); si llega pero no es
      // numérico también se cae al fallback en vez de propagar NaN a
      // DataTable.totalPages.
      const rawHeaderTotal = res.headers.get("X-Total-Count");
      const headerTotal = rawHeaderTotal === null ? NaN : Number(rawHeaderTotal);
      total = Number.isFinite(headerTotal) ? headerTotal : Number(body.total ?? 0);
    } else {
      const body = await res.json().catch(() => ({}));
      // El detail de un 422 de FastAPI puede venir como lista de dicts
      // (RequestValidationError) en vez de string — no se propaga tal cual
      // a un {@html}/interpolación de texto (M-3).
      loadError =
        typeof body.detail === "string"
          ? body.detail
          : `Error ${res.status} al cargar los expedientes.`;
    }
  } catch {
    loadError = "No se pudo conectar al servidor de expedientes.";
  }

  return {
    expedientes,
    total,
    loadError,
    initialFilters,
    // token y tenant NO se exponen (D-05) — no hace falta: no hay acciones
    // client-side en esta vista, solo navegación (goto) y el form action `create`.
  };
}

// ---------------------------------------------------------------------------
// actions.create — creación de expediente (form action, sin +server.js)
// ---------------------------------------------------------------------------

/** @type {import('./$types').Actions} */
export const actions = {
  /**
   * POST /api/v1/expedientes — crea un expediente nuevo.
   *
   * Allowlist estricta (anti mass-assignment): solo se reenvían los campos
   * declarados aquí, construidos uno a uno desde el FormData — nunca se
   * reenvía el body del cliente tal cual.
   *
   * `code` es server-generated: el backend lo asigna, NUNCA se envía desde
   * el front.
   *
   * v1 (E22 T-12): sin Form Builder, `metadata` va siempre `{}`. El nivel de
   * seguridad no es seleccionable desde este formulario: SessionUser (JWT +
   * /auth/me) no expone el clearance del usuario en el front, así que no hay
   * forma de acotar el <select> a los niveles que el usuario podría leer de
   * vuelta (decisión de orfeo-architect). Se fija nivel 1 (PÚBLICA) — la
   * opción visible sin conocer el clearance. El bloque de opciones queda
   * comentado para cuando el clearance esté disponible en el front.
   */
  create: async ({ request, cookies, locals }) => {
    const token = locals.accessToken ?? cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
    if (!token) {
      return fail(401, { error: "unauthenticated", detail: "Sesión no válida." });
    }
    const tenant = locals.user?.tenant_slug ?? "";

    const formData = await request.formData();
    const name = String(formData.get("name") ?? "").trim();
    const description = String(formData.get("description") ?? "").trim();
    const trdSerieId = String(formData.get("trd_serie_id") ?? "").trim();

    if (!name) {
      return fail(400, {
        error: "validation_error",
        detail: "El nombre del expediente es obligatorio.",
        values: { name, description, trdSerieId },
      });
    }
    if (name.length > 500) {
      return fail(400, {
        error: "validation_error",
        detail: "El nombre no puede superar los 500 caracteres.",
        values: { name, description, trdSerieId },
      });
    }

    /** @type {Record<string, unknown>} */
    const payload = {
      name,
      // Nivel de seguridad fijo v1 — ver nota arriba. NUNCA se envía `code`.
      nivel_seguridad: 1,
      metadata: {},
    };
    if (description) payload.description = description;
    if (trdSerieId) payload.trd_serie_id = trdSerieId;

    let res;
    try {
      res = await fetch(`${API_V1}/expedientes`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token}`,
          "X-Tenant-Slug": tenant,
        },
        body: JSON.stringify(payload),
      });
    } catch {
      return fail(503, {
        error: "connection_error",
        detail: "No se pudo conectar al servidor de expedientes.",
        values: { name, description, trdSerieId },
      });
    }

    const body = await res.json().catch(() => ({}));

    if (!res.ok) {
      // 422 metadata_validation_failed (serie TRD con plantilla activa) u
      // otro error del backend: se propaga el detail al formulario. Un 422 de
      // validación de FastAPI (RequestValidationError, sin handler propio en
      // archive-service) trae `detail` como LISTA de dicts, no string —
      // interpolarlo tal cual en {form.detail} renderiza "[object Object]"
      // (M-3). Se normaliza a un mensaje legible en ese caso.
      const detail =
        typeof body.detail === "string"
          ? body.detail
          : `Error ${res.status} al crear el expediente.`;
      return fail(res.status, {
        error: body.error ?? "upstream_error",
        detail,
        values: { name, description, trdSerieId },
      });
    }

    // Éxito, pero si el upstream responde 2xx sin `id` (o un body no-JSON,
    // ya cubierto por el .catch de arriba) no hay a dónde redirigir — evita
    // un redirect(303, "/expedientes/undefined").
    if (typeof body.id !== "string" || !body.id) {
      return fail(502, {
        error: "invalid_upstream_response",
        detail: "El expediente se creó pero el servidor no devolvió su identificador.",
        values: { name, description, trdSerieId },
      });
    }

    // Éxito: redirige al workspace del expediente recién creado (T-10).
    redirect(303, `/expedientes/${body.id}`);
  },
};
