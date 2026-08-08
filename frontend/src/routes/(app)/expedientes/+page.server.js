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
 * Carga además `trdSeries` (GET /api/v1/trd?is_active=true&size=100 — mismo
 * endpoint que radicar/+page.server.js) para poblar el `<select>` de serie
 * TRD del Modal de creación (T-12 Form Builder). Se carga siempre (no solo
 * si `canManage`): es una llamada pequeña y gatearla por permiso añadiría
 * una segunda ruta de degradación sin beneficio real.
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
import { coerceSchemaValue } from "$lib/utils/schemaField.js";

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
      trdSeries: [],
      tvdAgrupaciones: [],
    };
  }

  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant,
  };

  const params = new URLSearchParams({ page: String(page), size: String(PAGE_SIZE) });
  // Trampa del contrato: min_length=2 en el backend — omitir `q` por debajo
  // de 2 caracteres. Enviar `q=` o `q=a` da 422.
  if (q.length >= 2) params.set("q", q);
  if (status) params.set("status", status);
  if (dateFrom) params.set("date_from", dateFrom);
  if (dateTo) params.set("date_to", dateTo);

  // Serie TRD: catálogo para el <select> del Modal de creación (T-12 Form
  // Builder). Se carga en paralelo con la búsqueda; si falla, el <select>
  // degrada a solo la opción "sin serie" (el Modal sigue funcionando).
  // Envueltos en una función async: si `fetch` lanza SÍNCRONAMENTE (algunos
  // mocks de test lo hacen), Promise.allSettled solo atrapa rechazos — un
  // throw síncrono al evaluar los argumentos del array se propagaría sin
  // pasar por allSettled.
  // TVD (ADR-026): catálogo SEPARADO del de TRD, no un filtro sobre él. El
  // backend las sirve por rutas distintas y desde vistas distintas
  // (`v_trd_series` / `v_tvd_agrupaciones`), así que aquí también viajan
  // separadas: mezclarlas en una lista y filtrarla en el cliente sería
  // reintroducir en el front la consulta de la clase "filtro olvidado" que el
  // ADR sacó de la base. Si la petición falla, el selector de fondo acumulado
  // queda vacío y el formulario lo dice — no cae al catálogo de TRD.
  const [searchRes, trdRes, tvdRes] = await Promise.allSettled([
    (async () => fetch(`${API_V1}/expedientes/search?${params.toString()}`, { headers }))(),
    (async () => fetch(`${API_V1}/trd?is_active=true&size=100`, { headers }))(),
    (async () => fetch(`${API_V1}/tvd?is_active=true&size=100`, { headers }))(),
  ]);

  let trdSeries = [];
  if (trdRes.status === "fulfilled" && trdRes.value.ok) {
    trdSeries = await trdRes.value.json().catch(() => []);
  }

  let tvdAgrupaciones = [];
  if (tvdRes.status === "fulfilled" && tvdRes.value.ok) {
    tvdAgrupaciones = await tvdRes.value.json().catch(() => []);
  }

  let expedientes = [];
  let total = 0;
  let loadError = null;

  try {
    if (searchRes.status === "rejected") throw searchRes.reason;
    const res = searchRes.value;

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
    trdSeries,
    tvdAgrupaciones,
    // token y tenant NO se exponen (D-05) — no hace falta: la única llamada
    // client-side de esta vista es GET /expedientes/api/metadata-templates
    // (D-05, lee la cookie httpOnly en el servidor), más la navegación
    // (goto) y el form action `create`.
  };
}

// ---------------------------------------------------------------------------
// actions.create — creación de expediente (form action, sin +server.js)
// ---------------------------------------------------------------------------

/**
 * Extrae `{error, detail}` de una respuesta de error del backend.
 *
 * Soporta dos formas: la plana observada normalmente en este front
 * (`{error, detail: <string>}`) y la forma ANIDADA que produce FastAPI al
 * serializar `HTTPException(detail={...})` tal como lo hace archive-service
 * en `create_expediente` para `metadata_validation_failed`
 * (`{detail: {error, detail: <string>, status}}` — expedientes.py:104-112,
 * metadata_service.py:46, contrato orfeo-architect §"Hecho verificado"). Un
 * 422 de FastAPI sin handler propio (RequestValidationError) trae `detail`
 * como LISTA de dicts, no string ni objeto con `.detail` — cae al mensaje
 * genérico en vez de interpolar "[object Object]" (M-3).
 * @param {any} body
 * @param {Response} res
 */
function extractErrorDetail(body, res) {
  if (typeof body.detail === "string") {
    return { error: body.error ?? "upstream_error", detail: body.detail };
  }
  if (body.detail && typeof body.detail === "object" && !Array.isArray(body.detail)) {
    const nestedDetail = body.detail.detail;
    if (typeof nestedDetail === "string") {
      return { error: body.detail.error ?? body.error ?? "upstream_error", detail: nestedDetail };
    }
  }
  return {
    error: body.error ?? "upstream_error",
    detail: `Error ${res.status} al crear el expediente.`,
  };
}

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
   * `metadata` (T-12 Form Builder): si se eligió una serie TRD, se re-obtiene
   * su plantilla ACTIVA de metadatos (GET .../expediente-metadata/templates)
   * — la MISMA fuente de verdad que el cliente usó para renderizar los
   * campos, pero re-consultada server-side, autoritativa — y se construye
   * `metadata` campo a campo SOLO con las claves que el `json_schema`
   * declara (doble allowlist: prefijo `meta_` del FormData Y pertenencia al
   * schema), coercionando cada valor a su tipo declarado
   * (`$lib/utils/schemaField.js#coerceSchemaValue`). Sin serie o sin
   * plantilla activa, `metadata` queda `{}` (comportamiento v1 preservado).
   *
   * El nivel de seguridad no es seleccionable desde este formulario:
   * SessionUser (JWT + /auth/me) no expone el clearance del usuario en el
   * front, así que no hay forma de acotar el <select> a los niveles que el
   * usuario podría leer de vuelta (decisión de orfeo-architect). Se fija
   * nivel 1 (PÚBLICA) — la opción visible sin conocer el clearance. El
   * bloque de opciones queda comentado en +page.svelte para cuando el
   * clearance esté disponible en el front.
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
    // ADR-026: `origen` decide bajo qué INSTRUMENTO puede cerrarse este
    // expediente y, en fondo acumulado, desde qué FECHA se cuenta su
    // retención. Se normaliza contra un conjunto cerrado: cualquier otro
    // valor cae a "corriente", que es la opción conservadora — nunca se
    // deduce "fondo acumulado" de un campo inesperado.
    const origenRaw = String(formData.get("origen") ?? "").trim();
    const origen = origenRaw === "fondo_acumulado" ? "fondo_acumulado" : "corriente";
    const fechaExtremaInicial = String(formData.get("fecha_extrema_inicial") ?? "").trim();
    const fechaExtremaFinal = String(formData.get("fecha_extrema_final") ?? "").trim();

    /** Valores para repoblar el formulario si algo falla. */
    const formValues = {
      name,
      description,
      trdSerieId,
      origen,
      fechaExtremaInicial,
      fechaExtremaFinal,
    };

    if (!name) {
      return fail(400, {
        error: "validation_error",
        detail: "El nombre del expediente es obligatorio.",
        values: formValues,
      });
    }
    if (name.length > 500) {
      return fail(400, {
        error: "validation_error",
        detail: "El nombre no puede superar los 500 caracteres.",
        values: formValues,
      });
    }

    // Espejo de `validar_forma_fondo_acumulado` (archive-service): el backend
    // vuelve a validarlo y la base lo blinda con dos CHECK — esto solo evita
    // el viaje y da un mensaje en el idioma del formulario. El mensaje NO se
    // inventa: dice por qué la fecha importa, porque es la base de un
    // calendario de retención que después es irreversible.
    if (origen === "fondo_acumulado") {
      if (!fechaExtremaFinal) {
        return fail(400, {
          error: "validation_error",
          detail:
            "La fecha extrema final es obligatoria en un fondo acumulado: es la fecha desde la que se cuenta su retención.",
          values: formValues,
        });
      }
      if (fechaExtremaInicial && fechaExtremaInicial > fechaExtremaFinal) {
        return fail(400, {
          error: "validation_error",
          detail: "La fecha extrema inicial no puede ser posterior a la final.",
          values: formValues,
        });
      }
      if (!trdSerieId) {
        return fail(400, {
          error: "validation_error",
          detail: "Un fondo acumulado debe clasificarse bajo una agrupación de TVD.",
          values: formValues,
        });
      }
    } else if (fechaExtremaInicial || fechaExtremaFinal) {
      return fail(400, {
        error: "validation_error",
        detail: "Las fechas extremas solo aplican a un expediente de fondo acumulado.",
        values: formValues,
      });
    }

    // -------------------------------------------------------------------
    // metadata: allowlist campo a campo desde el json_schema de la
    // plantilla ACTIVA de la serie TRD elegida (nunca un passthrough del
    // FormData). `metaRawValues` guarda los strings crudos (pre-coerción)
    // SOLO para repoblar el formulario si el backend rechaza la creación —
    // no se reenvían tal cual al backend.
    // -------------------------------------------------------------------
    /** @type {Record<string, unknown>} */
    const metadata = {};
    /** @type {Record<string, string>} */
    const metaRawValues = {};

    if (trdSerieId) {
      let tplRes;
      try {
        tplRes = await fetch(
          `${API_V1}/expediente-metadata/templates?trd_serie_id=${encodeURIComponent(trdSerieId)}`,
          { headers: { Authorization: `Bearer ${token}`, "X-Tenant-Slug": tenant } },
        );
      } catch {
        return fail(503, {
          error: "connection_error",
          detail: "No se pudo conectar al servidor de plantillas de metadatos.",
          values: formValues,
        });
      }

      if (tplRes.ok) {
        const list = await tplRes.json().catch(() => []);
        const tpl = Array.isArray(list) ? list.find((t) => t?.activo) : null;
        const properties = tpl?.json_schema?.properties;
        if (properties && typeof properties === "object") {
          for (const key of Object.keys(properties)) {
            const raw = String(formData.get(`meta_${key}`) ?? "");
            if (raw !== "") metaRawValues[key] = raw;
            const coerced = coerceSchemaValue(properties[key], raw);
            if (coerced !== undefined) metadata[key] = coerced;
          }
        }
      } else if (tplRes.status >= 500) {
        // Fallo real del servicio de plantillas: NO se descarta en silencio
        // lo que el usuario ya escribió — se le pide reintentar.
        return fail(503, {
          error: "connection_error",
          detail: "No se pudo cargar la plantilla de metadatos de la serie TRD.",
          values: { ...formValues, meta: metaRawValues },
        });
      }
      // 4xx no-500 (serie sin plantilla activa, por ejemplo): metadata
      // queda {} — comportamiento v1 preservado, el backend igual valida.
    }

    /** @type {Record<string, unknown>} */
    const payload = {
      name,
      // Nivel de seguridad fijo v1 — ver nota arriba. NUNCA se envía `code`.
      nivel_seguridad: 1,
      metadata,
    };
    if (description) payload.description = description;
    if (trdSerieId) payload.trd_serie_id = trdSerieId;
    if (origen === "fondo_acumulado") {
      payload.origen = origen;
      payload.fecha_extrema_final = fechaExtremaFinal;
      if (fechaExtremaInicial) payload.fecha_extrema_inicial = fechaExtremaInicial;
    }

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
        values: { ...formValues, meta: metaRawValues },
      });
    }

    const body = await res.json().catch(() => ({}));

    if (!res.ok) {
      // 422 metadata_validation_failed (serie TRD con plantilla activa) u
      // otro error del backend: se propaga el detail al formulario.
      const { error, detail } = extractErrorDetail(body, res);
      return fail(res.status, {
        error,
        detail,
        values: { ...formValues, meta: metaRawValues },
      });
    }

    // Éxito, pero si el upstream responde 2xx sin `id` (o un body no-JSON,
    // ya cubierto por el .catch de arriba) no hay a dónde redirigir — evita
    // un redirect(303, "/expedientes/undefined").
    if (typeof body.id !== "string" || !body.id) {
      return fail(502, {
        error: "invalid_upstream_response",
        detail: "El expediente se creó pero el servidor no devolvió su identificador.",
        values: { ...formValues, meta: metaRawValues },
      });
    }

    // Éxito: redirige al workspace del expediente recién creado (T-10).
    redirect(303, `/expedientes/${body.id}`);
  },
};
