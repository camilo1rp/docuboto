import { f as fail, r as redirect } from "../../../../chunks/index.js";
import { C as COOKIE_ACCESS_TOKEN } from "../../../../chunks/auth.server.js";
import { A as API_V1 } from "../../../../chunks/config.server.js";
import { c as coerceSchemaValue } from "../../../../chunks/schemaField.js";
const STATUS_ALLOWLIST = ["open", "closed", "transferred"];
const DATE_RE = /^\d{4}-\d{2}-\d{2}$/;
const PAGE_RE = /^\d{1,5}$/;
const PAGE_SIZE = 20;
const Q_MAX_LENGTH = 200;
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
async function load({ cookies, parent, url, fetch: fetch2 }) {
  const { user } = await parent();
  const token = cookies.get(COOKIE_ACCESS_TOKEN) ?? "";
  const tenant = user?.tenant_slug ?? "";
  const { q, status, dateFrom, dateTo, page } = readFilters(url);
  const initialFilters = { q, status, dateFrom, dateTo, page };
  if (!token) {
    return {
      expedientes: [],
      total: 0,
      loadError: "Sesión no válida. Vuelve a iniciar sesión.",
      initialFilters,
      trdSeries: []
    };
  }
  const headers = {
    Authorization: `Bearer ${token}`,
    "X-Tenant-Slug": tenant
  };
  const params = new URLSearchParams({ page: String(page), size: String(PAGE_SIZE) });
  if (q.length >= 2) params.set("q", q);
  if (status) params.set("status", status);
  if (dateFrom) params.set("date_from", dateFrom);
  if (dateTo) params.set("date_to", dateTo);
  const [searchRes, trdRes] = await Promise.allSettled([
    (async () => fetch2(`${API_V1}/expedientes/search?${params.toString()}`, { headers }))(),
    (async () => fetch2(`${API_V1}/trd?is_active=true&size=100`, { headers }))()
  ]);
  let trdSeries = [];
  if (trdRes.status === "fulfilled" && trdRes.value.ok) {
    trdSeries = await trdRes.value.json().catch(() => []);
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
      const rawHeaderTotal = res.headers.get("X-Total-Count");
      const headerTotal = rawHeaderTotal === null ? NaN : Number(rawHeaderTotal);
      total = Number.isFinite(headerTotal) ? headerTotal : Number(body.total ?? 0);
    } else {
      const body = await res.json().catch(() => ({}));
      loadError = typeof body.detail === "string" ? body.detail : `Error ${res.status} al cargar los expedientes.`;
    }
  } catch {
    loadError = "No se pudo conectar al servidor de expedientes.";
  }
  return {
    expedientes,
    total,
    loadError,
    initialFilters,
    trdSeries
    // token y tenant NO se exponen (D-05) — no hace falta: la única llamada
    // client-side de esta vista es GET /expedientes/api/metadata-templates
    // (D-05, lee la cookie httpOnly en el servidor), más la navegación
    // (goto) y el form action `create`.
  };
}
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
    detail: `Error ${res.status} al crear el expediente.`
  };
}
const actions = {
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
    if (!name) {
      return fail(400, {
        error: "validation_error",
        detail: "El nombre del expediente es obligatorio.",
        values: { name, description, trdSerieId }
      });
    }
    if (name.length > 500) {
      return fail(400, {
        error: "validation_error",
        detail: "El nombre no puede superar los 500 caracteres.",
        values: { name, description, trdSerieId }
      });
    }
    const metadata = {};
    const metaRawValues = {};
    if (trdSerieId) {
      let tplRes;
      try {
        tplRes = await fetch(
          `${API_V1}/expediente-metadata/templates?trd_serie_id=${encodeURIComponent(trdSerieId)}`,
          { headers: { Authorization: `Bearer ${token}`, "X-Tenant-Slug": tenant } }
        );
      } catch {
        return fail(503, {
          error: "connection_error",
          detail: "No se pudo conectar al servidor de plantillas de metadatos.",
          values: { name, description, trdSerieId }
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
            if (coerced !== void 0) metadata[key] = coerced;
          }
        }
      } else if (tplRes.status >= 500) {
        return fail(503, {
          error: "connection_error",
          detail: "No se pudo cargar la plantilla de metadatos de la serie TRD.",
          values: { name, description, trdSerieId, meta: metaRawValues }
        });
      }
    }
    const payload = {
      name,
      // Nivel de seguridad fijo v1 — ver nota arriba. NUNCA se envía `code`.
      nivel_seguridad: 1,
      metadata
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
          "X-Tenant-Slug": tenant
        },
        body: JSON.stringify(payload)
      });
    } catch {
      return fail(503, {
        error: "connection_error",
        detail: "No se pudo conectar al servidor de expedientes.",
        values: { name, description, trdSerieId, meta: metaRawValues }
      });
    }
    const body = await res.json().catch(() => ({}));
    if (!res.ok) {
      const { error, detail } = extractErrorDetail(body, res);
      return fail(res.status, {
        error,
        detail,
        values: { name, description, trdSerieId, meta: metaRawValues }
      });
    }
    if (typeof body.id !== "string" || !body.id) {
      return fail(502, {
        error: "invalid_upstream_response",
        detail: "El expediente se creó pero el servidor no devolvió su identificador.",
        values: { name, description, trdSerieId, meta: metaRawValues }
      });
    }
    redirect(303, `/expedientes/${body.id}`);
  }
};
export {
  actions,
  load
};
