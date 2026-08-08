// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * expedientes-server.test.js — +page.server.js del listado de expedientes (T-12).
 *
 * Cubre:
 *  - Guard de sesión del `load` (401/sin token): no debe llamar al gateway.
 *  - Saneado de query params contra la allowlist (status inválido, q<2 chars,
 *    fechas mal formadas) antes de construir la URL de /expedientes/search.
 *  - Parseo de la respuesta (hits[]/X-Total-Count) y manejo de error upstream.
 *  - `actions.create`: guard de sesión, validación de `name`, allowlist
 *    anti mass-assignment del payload (nunca `code`, nivel_seguridad fijo en
 *    1), passthrough del detail en error 422, y redirect a /expedientes/{id}
 *    en éxito.
 */
import { describe, it, expect, vi, afterEach } from "vitest";
import { load, actions } from "./+page.server.js";

const url = (path = "/expedientes") => new URL(`http://localhost${path}`);

/** Construye un `event.fetch` que responde con lo indicado y captura la llamada. */
function fakeFetch({ ok = true, status = 200, body = {}, headers = {} } = {}) {
  const calls = [];
  const fn = vi.fn(async (input, init) => {
    calls.push({ input, init });
    return {
      ok,
      status,
      headers: { get: (name) => headers[name] ?? null },
      json: async () => body,
    };
  });
  fn.calls = calls;
  return fn;
}

/** `fetch` que revienta si se invoca — prueba que el guard corre ANTES de llamar al gateway. */
function fetchThatMustNotBeCalled() {
  return vi.fn(() => {
    throw new Error("fetch no debió invocarse sin sesión");
  });
}

const parentWithUser =
  (tenant_slug = "icetex") =>
  async () => ({ user: { tenant_slug } });

describe("expedientes/+page.server.js#load — guard de sesión (D-05)", () => {
  it("sin access_token: no llama al gateway y expone loadError", async () => {
    const result = await load({
      cookies: { get: () => undefined },
      parent: parentWithUser(),
      url: url(),
      fetch: fetchThatMustNotBeCalled(),
    });

    expect(result.loadError).toBeTruthy();
    expect(result.expedientes).toEqual([]);
    expect(result.total).toBe(0);
  });

  it("un access_token vacío también se trata como sin sesión", async () => {
    const result = await load({
      cookies: { get: () => "" },
      parent: parentWithUser(),
      url: url(),
      fetch: fetchThatMustNotBeCalled(),
    });

    expect(result.loadError).toBeTruthy();
  });
});

describe("expedientes/+page.server.js#load — saneado de query params (allowlist)", () => {
  it("status fuera de la allowlist se descarta (no se reenvía al gateway)", async () => {
    const fetch = fakeFetch({ body: { hits: [], total: 0 } });
    await load({
      cookies: { get: () => "tok" },
      parent: parentWithUser(),
      url: url("/expedientes?status=deleted_forever"),
      fetch,
    });

    const calledUrl = fetch.calls[0].input;
    expect(calledUrl).not.toContain("status=");
  });

  it("status válido (closed) se reenvía tal cual", async () => {
    const fetch = fakeFetch({ body: { hits: [], total: 0 } });
    await load({
      cookies: { get: () => "tok" },
      parent: parentWithUser(),
      url: url("/expedientes?status=closed"),
      fetch,
    });

    expect(fetch.calls[0].input).toContain("status=closed");
  });

  it("q con menos de 2 caracteres se omite del upstream (evita 422 por min_length)", async () => {
    const fetch = fakeFetch({ body: { hits: [], total: 0 } });
    await load({
      cookies: { get: () => "tok" },
      parent: parentWithUser(),
      url: url("/expedientes?q=a"),
      fetch,
    });

    expect(fetch.calls[0].input).not.toContain("q=");
  });

  it("q con 2+ caracteres se envía recortado (trim)", async () => {
    const fetch = fakeFetch({ body: { hits: [], total: 0 } });
    await load({
      cookies: { get: () => "tok" },
      parent: parentWithUser(),
      url: url("/expedientes?q=%20contrato%20"),
      fetch,
    });

    expect(fetch.calls[0].input).toContain("q=contrato");
  });

  it("fechas mal formadas se descartan; fechas ISO válidas se conservan", async () => {
    const fetch = fakeFetch({ body: { hits: [], total: 0 } });
    await load({
      cookies: { get: () => "tok" },
      parent: parentWithUser(),
      url: url("/expedientes?date_from=not-a-date&date_to=2026-01-31"),
      fetch,
    });

    expect(fetch.calls[0].input).not.toContain("date_from=");
    expect(fetch.calls[0].input).toContain("date_to=2026-01-31");
  });

  it("size siempre es 20, sin importar lo que pida la URL", async () => {
    const fetch = fakeFetch({ body: { hits: [], total: 0 } });
    await load({
      cookies: { get: () => "tok" },
      parent: parentWithUser(),
      url: url("/expedientes?size=999"),
      fetch,
    });

    expect(fetch.calls[0].input).toContain("size=20");
    expect(fetch.calls[0].input).not.toContain("size=999");
  });

  it("page < 1 o no numérica cae a 1", async () => {
    const fetch = fakeFetch({ body: { hits: [], total: 0 } });
    await load({
      cookies: { get: () => "tok" },
      parent: parentWithUser(),
      url: url("/expedientes?page=-5"),
      fetch,
    });

    expect(fetch.calls[0].input).toContain("page=1");
  });

  // M-2: Number("1e9") es finito y >=1 — sin una regex de dígitos puros se
  // reenviaría page=1000000000 al gateway (OFFSET 19999999980 en el repo).
  it.each([["abc"], ["1e9"], [""], [" 1 "], ["0x10"], ["+5"], ["1.5"], ["99999999999999999999"]])(
    "page=%s (no dígitos puros 1-5 chars) cae a 1",
    async (rawPage) => {
      const fetch = fakeFetch({ body: { hits: [], total: 0 } });
      await load({
        cookies: { get: () => "tok" },
        parent: parentWithUser(),
        url: url(`/expedientes?page=${encodeURIComponent(rawPage)}`),
        fetch,
      });

      expect(fetch.calls[0].input).toContain("page=1&");
    },
  );

  it("q se recorta a 200 caracteres (sin tope explícito, el backend tampoco lo tiene)", async () => {
    const fetch = fakeFetch({ body: { hits: [], total: 0 } });
    const longQ = "a".repeat(500);
    await load({
      cookies: { get: () => "tok" },
      parent: parentWithUser(),
      url: url(`/expedientes?q=${longQ}`),
      fetch,
    });

    const sentUrl = fetch.calls[0].input;
    const sentQ = new URL(sentUrl).searchParams.get("q");
    expect(sentQ).toHaveLength(200);
  });
});

describe("expedientes/+page.server.js#load — respuesta del gateway", () => {
  it("parsea hits[] y usa X-Total-Count cuando está presente", async () => {
    const fetch = fakeFetch({
      body: { hits: [{ id: "e1", code: "2026-ICETEX-EXP-000001" }], total: 1 },
      headers: { "X-Total-Count": "42" },
    });
    const result = await load({
      cookies: { get: () => "tok" },
      parent: parentWithUser(),
      url: url(),
      fetch,
    });

    expect(result.expedientes).toHaveLength(1);
    expect(result.total).toBe(42);
    expect(result.loadError).toBeNull();
  });

  // M-6: el gateway NO reenvía X-Total-Count (no está en
  // FORWARDED_RESPONSE_HEADERS, services/api-gateway/app/core/proxy.py) — en
  // producción SIEMPRE se usa este camino (sin header), no el de arriba.
  it("sin header X-Total-Count, usa el fallback body.total (camino real en producción)", async () => {
    const fetch = fakeFetch({
      body: { hits: [{ id: "e1", code: "2026-ICETEX-EXP-000001" }], total: 42 },
    });
    const result = await load({
      cookies: { get: () => "tok" },
      parent: parentWithUser(),
      url: url(),
      fetch,
    });

    expect(result.total).toBe(42);
  });

  it("header X-Total-Count no numérico también cae al fallback body.total (no propaga NaN)", async () => {
    const fetch = fakeFetch({
      body: { hits: [], total: 7 },
      headers: { "X-Total-Count": "not-a-number" },
    });
    const result = await load({
      cookies: { get: () => "tok" },
      parent: parentWithUser(),
      url: url(),
      fetch,
    });

    expect(result.total).toBe(7);
  });

  it("respuesta no-ok expone loadError con el detail del backend", async () => {
    const fetch = fakeFetch({ ok: false, status: 403, body: { detail: "Sin permisos" } });
    const result = await load({
      cookies: { get: () => "tok" },
      parent: parentWithUser(),
      url: url(),
      fetch,
    });

    expect(result.loadError).toBe("Sin permisos");
    expect(result.expedientes).toEqual([]);
  });

  it("detail no-string (422 RequestValidationError: lista de dicts) se normaliza a un mensaje legible (M-3)", async () => {
    const fetch = fakeFetch({
      ok: false,
      status: 422,
      body: {
        detail: [{ loc: ["query", "trd_serie_id"], msg: "invalid uuid", type: "value_error" }],
      },
    });
    const result = await load({
      cookies: { get: () => "tok" },
      parent: parentWithUser(),
      url: url(),
      fetch,
    });

    expect(result.loadError).toBe("Error 422 al cargar los expedientes.");
  });

  it("un fallo de red produce un loadError genérico sin lanzar", async () => {
    const fetch = vi.fn(() => {
      throw new Error("network down");
    });
    const result = await load({
      cookies: { get: () => "tok" },
      parent: parentWithUser(),
      url: url(),
      fetch,
    });

    expect(result.loadError).toBeTruthy();
  });
});

describe("expedientes/+page.server.js#actions.create — guard de sesión (D-05)", () => {
  it("sin locals.accessToken ni cookie: 401 antes de leer el FormData", async () => {
    const request = {
      formData: () => {
        throw new Error("formData() no debió invocarse sin sesión");
      },
    };
    const result = await actions.create({
      request,
      cookies: { get: () => undefined },
      locals: {},
    });

    expect(result.status).toBe(401);
    expect(result.data.error).toBe("unauthenticated");
  });
});

describe("expedientes/+page.server.js#actions.create — validación", () => {
  it("nombre vacío: 400 validation_error", async () => {
    const fd = new FormData();
    fd.set("name", "   ");
    const result = await actions.create({
      request: { formData: async () => fd },
      cookies: { get: () => "tok" },
      locals: { accessToken: "tok", user: { tenant_slug: "icetex" } },
    });

    expect(result.status).toBe(400);
    expect(result.data.error).toBe("validation_error");
  });

  it("nombre de más de 500 caracteres: 400 validation_error", async () => {
    const fd = new FormData();
    fd.set("name", "x".repeat(501));
    const result = await actions.create({
      request: { formData: async () => fd },
      cookies: { get: () => "tok" },
      locals: { accessToken: "tok", user: { tenant_slug: "icetex" } },
    });

    expect(result.status).toBe(400);
  });
});

describe("expedientes/+page.server.js#actions.create — fondo acumulado (ADR-026)", () => {
  const ctx = () => ({
    cookies: { get: () => "tok" },
    locals: { accessToken: "tok", user: { tenant_slug: "icetex" } },
  });

  it("fondo acumulado sin fecha extrema final: 400 antes de llamar al gateway", async () => {
    const fd = new FormData();
    fd.set("name", "Fondo de la entidad liquidada");
    fd.set("origen", "fondo_acumulado");
    fd.set("trd_serie_id", "11111111-1111-1111-1111-111111111111");

    const result = await actions.create({ request: { formData: async () => fd }, ...ctx() });

    expect(result.status).toBe(400);
    expect(result.data.error).toBe("validation_error");
    // Repoblación completa: sin esto el usuario pierde el origen y las fechas
    // que ya había escrito (mismo defecto que las 7 pantallas de /admin).
    expect(result.data.values.origen).toBe("fondo_acumulado");
  });

  it("fondo acumulado sin instrumento: 400 (debe apuntar a una TVD)", async () => {
    const fd = new FormData();
    fd.set("name", "Fondo sin instrumento");
    fd.set("origen", "fondo_acumulado");
    fd.set("fecha_extrema_final", "1998-12-31");

    const result = await actions.create({ request: { formData: async () => fd }, ...ctx() });
    expect(result.status).toBe(400);
  });

  it("fecha inicial posterior a la final: 400", async () => {
    const fd = new FormData();
    fd.set("name", "Fondo con fechas invertidas");
    fd.set("origen", "fondo_acumulado");
    fd.set("trd_serie_id", "11111111-1111-1111-1111-111111111111");
    fd.set("fecha_extrema_inicial", "1999-01-01");
    fd.set("fecha_extrema_final", "1998-12-31");

    const result = await actions.create({ request: { formData: async () => fd }, ...ctx() });
    expect(result.status).toBe(400);
  });

  it("expediente corriente con fechas extremas: 400 (la negación declarada)", async () => {
    const fd = new FormData();
    fd.set("name", "Corriente con fechas");
    fd.set("fecha_extrema_final", "1998-12-31");

    const result = await actions.create({ request: { formData: async () => fd }, ...ctx() });
    expect(result.status).toBe(400);
  });

  it("un `origen` inesperado NO se interpreta como fondo acumulado", async () => {
    // La normalización cae al valor conservador: un campo manipulado no puede
    // convertir un expediente corriente en un fondo acumulado (que cambia el
    // instrumento y la fecha base del calendario de retención).
    const fd = new FormData();
    fd.set("name", "Origen inesperado");
    fd.set("origen", "FONDO_ACUMULADO");

    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 201,
      json: async () => ({ id: "exp-1" }),
      headers: { get: () => null },
    }));
    const original = globalThis.fetch;
    globalThis.fetch = fetchMock;
    try {
      // La creación con éxito termina en `redirect(303)`, que SvelteKit lanza:
      // se absorbe aquí porque lo que se prueba es el cuerpo enviado, no el
      // desenlace de la navegación.
      await actions.create({ request: { formData: async () => fd }, ...ctx() }).catch(() => {});
    } finally {
      globalThis.fetch = original;
    }

    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body.origen).toBeUndefined();
    expect(body.fecha_extrema_final).toBeUndefined();
  });
});

describe("expedientes/+page.server.js#actions.create — allowlist anti mass-assignment", () => {
  // El handler llama al `fetch` global (mismo patrón que los demás proxies de
  // escritura del proyecto, p. ej. radicar/api/documents/+server.js) — se
  // stubea globalThis.fetch en vez de pasar `fetch` por el evento.
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it("solo reenvía name/description/trd_serie_id/nivel_seguridad/metadata; nunca `code`", async () => {
    const fd = new FormData();
    fd.set("name", "Contrato de arrendamiento");
    fd.set("description", "Expediente de prueba");
    fd.set("trd_serie_id", "11111111-1111-1111-1111-111111111111");
    // Campos que NO deben colarse en el payload (intento de mass-assignment)
    fd.set("code", "2026-ICETEX-EXP-999999");
    fd.set("nivel_seguridad", "3");
    fd.set("status", "closed");
    fd.set("metadata", JSON.stringify({ evil: true }));

    let capturedInit;
    globalThis.fetch = vi.fn(async (_input, init) => {
      capturedInit = init;
      return { ok: true, status: 201, json: async () => ({ id: "exp-nuevo-1" }) };
    });

    let thrown;
    try {
      await actions.create({
        request: { formData: async () => fd },
        cookies: { get: () => "tok" },
        locals: { accessToken: "tok", user: { tenant_slug: "icetex" } },
      });
    } catch (err) {
      thrown = err;
    }

    const payload = JSON.parse(capturedInit.body);
    expect(payload).toEqual({
      name: "Contrato de arrendamiento",
      description: "Expediente de prueba",
      trd_serie_id: "11111111-1111-1111-1111-111111111111",
      nivel_seguridad: 1, // fijo en 1 (PÚBLICA) — nunca el 3 que envió el cliente
      metadata: {}, // nunca el objeto arbitrario que envió el cliente
    });
    expect(payload.code).toBeUndefined();
    expect(payload.status).toBeUndefined();

    // Éxito → redirect(303, /expedientes/{id}) — SvelteKit lo implementa como throw.
    expect(thrown).toEqual({ status: 303, location: "/expedientes/exp-nuevo-1" });
  });

  it("campos opcionales vacíos se omiten del payload (no se envían strings vacíos)", async () => {
    const fd = new FormData();
    fd.set("name", "Expediente mínimo");
    fd.set("description", "");
    fd.set("trd_serie_id", "");

    let capturedInit;
    globalThis.fetch = vi.fn(async (_input, init) => {
      capturedInit = init;
      return { ok: true, status: 201, json: async () => ({ id: "exp-nuevo-2" }) };
    });

    try {
      await actions.create({
        request: { formData: async () => fd },
        cookies: { get: () => "tok" },
        locals: { accessToken: "tok", user: { tenant_slug: "icetex" } },
      });
    } catch {
      // redirect esperado — no es el foco de este test
    }

    const payload = JSON.parse(capturedInit.body);
    expect(payload).toEqual({ name: "Expediente mínimo", nivel_seguridad: 1, metadata: {} });
  });

  it("422 metadata_validation_failed del backend propaga el detail al formulario", async () => {
    const fd = new FormData();
    fd.set("name", "Expediente con plantilla TRD");
    fd.set("trd_serie_id", "22222222-2222-2222-2222-222222222222");

    globalThis.fetch = vi.fn(async () => ({
      ok: false,
      status: 422,
      json: async () => ({
        error: "metadata_validation_failed",
        detail: "Falta el campo obligatorio 'monto_contrato' de la plantilla de la serie TRD.",
      }),
    }));

    const result = await actions.create({
      request: { formData: async () => fd },
      cookies: { get: () => "tok" },
      locals: { accessToken: "tok", user: { tenant_slug: "icetex" } },
    });

    expect(result.status).toBe(422);
    expect(result.data.error).toBe("metadata_validation_failed");
    expect(result.data.detail).toContain("monto_contrato");
  });

  it("422 con detail como lista de dicts (RequestValidationError sin handler propio) se normaliza a string (M-3)", async () => {
    const fd = new FormData();
    fd.set("name", "Expediente con trd_serie_id inválido");
    fd.set("trd_serie_id", "no-es-un-uuid");

    globalThis.fetch = vi.fn(async () => ({
      ok: false,
      status: 422,
      json: async () => ({
        detail: [{ loc: ["body", "trd_serie_id"], msg: "invalid uuid", type: "value_error" }],
      }),
    }));

    const result = await actions.create({
      request: { formData: async () => fd },
      cookies: { get: () => "tok" },
      locals: { accessToken: "tok", user: { tenant_slug: "icetex" } },
    });

    expect(result.status).toBe(422);
    expect(typeof result.data.detail).toBe("string");
    expect(result.data.detail).toBe("Error 422 al crear el expediente.");
  });

  it("2xx sin `id` en el body no redirige a /expedientes/undefined (502 invalid_upstream_response)", async () => {
    const fd = new FormData();
    fd.set("name", "Expediente con respuesta upstream rara");

    globalThis.fetch = vi.fn(async () => ({
      ok: true,
      status: 201,
      json: async () => ({}),
    }));

    const result = await actions.create({
      request: { formData: async () => fd },
      cookies: { get: () => "tok" },
      locals: { accessToken: "tok", user: { tenant_slug: "icetex" } },
    });

    expect(result.status).toBe(502);
    expect(result.data.error).toBe("invalid_upstream_response");
  });

  it("422 metadata_validation_failed en forma ANIDADA (HTTPException(detail={...}) de FastAPI) se lee correctamente", async () => {
    const fd = new FormData();
    fd.set("name", "Expediente con plantilla TRD");
    fd.set("trd_serie_id", "22222222-2222-2222-2222-222222222222");

    globalThis.fetch = vi.fn(async () => ({
      ok: false,
      status: 422,
      json: async () => ({
        detail: {
          error: "metadata_validation_failed",
          detail: "'monto_contrato' is a required property",
          status: 422,
        },
      }),
    }));

    const result = await actions.create({
      request: { formData: async () => fd },
      cookies: { get: () => "tok" },
      locals: { accessToken: "tok", user: { tenant_slug: "icetex" } },
    });

    expect(result.status).toBe(422);
    expect(result.data.error).toBe("metadata_validation_failed");
    expect(result.data.detail).toBe("'monto_contrato' is a required property");
  });
});

describe("expedientes/+page.server.js#actions.create — construcción de `metadata` (T-12 Form Builder)", () => {
  const originalFetch = globalThis.fetch;
  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  const SERIE_ID = "33333333-3333-3333-3333-333333333333";
  const SCHEMA = {
    json_schema: {
      properties: {
        motivo: { type: "string" },
        monto_contrato: { type: "number" },
        urgente: { type: "boolean" },
        fecha_limite: { type: "string", format: "date" },
        // No declarado en el schema — NO debe colarse en metadata aunque el
        // cliente envíe meta_no_declarado (allowlist anti mass-assignment).
      },
      required: ["motivo"],
    },
  };

  /** Instala un fetch fake: 1ª llamada = plantillas, 2ª = creación del expediente. */
  function installTemplateThenCreateFetch({ templateBody = [{ ...SCHEMA, activo: true }] } = {}) {
    const calls = [];
    globalThis.fetch = vi.fn(async (input, init) => {
      calls.push({ input, init });
      if (calls.length === 1) {
        return { ok: true, status: 200, json: async () => templateBody };
      }
      return { ok: true, status: 201, json: async () => ({ id: "exp-meta-1" }) };
    });
    return calls;
  }

  it("construye metadata SOLO con las claves declaradas por el json_schema, coercionando tipos", async () => {
    const calls = installTemplateThenCreateFetch();
    const fd = new FormData();
    fd.set("name", "Expediente con metadatos");
    fd.set("trd_serie_id", SERIE_ID);
    fd.set("meta_motivo", "Renovación de contrato");
    fd.set("meta_monto_contrato", "1500000.50");
    fd.set("meta_urgente", "true");
    fd.set("meta_fecha_limite", "2026-12-31");
    // Intento de mass-assignment: clave que NO está en el schema.
    fd.set("meta_no_declarado", "inyeccion");

    try {
      await actions.create({
        request: { formData: async () => fd },
        cookies: { get: () => "tok" },
        locals: { accessToken: "tok", user: { tenant_slug: "icetex" } },
      });
    } catch {
      // redirect esperado
    }

    const createPayload = JSON.parse(calls[1].init.body);
    expect(createPayload.metadata).toEqual({
      motivo: "Renovación de contrato",
      monto_contrato: 1500000.5,
      urgente: true,
      fecha_limite: "2026-12-31",
    });
    expect(createPayload.metadata.no_declarado).toBeUndefined();
  });

  it("campos meta_* vacíos se omiten de metadata (deja que el backend exija los required)", async () => {
    const calls = installTemplateThenCreateFetch();
    const fd = new FormData();
    fd.set("name", "Expediente con metadatos parciales");
    fd.set("trd_serie_id", SERIE_ID);
    fd.set("meta_motivo", "Motivo mínimo");
    // monto_contrato/urgente/fecha_limite: no se envían

    try {
      await actions.create({
        request: { formData: async () => fd },
        cookies: { get: () => "tok" },
        locals: { accessToken: "tok", user: { tenant_slug: "icetex" } },
      });
    } catch {
      // redirect esperado
    }

    const createPayload = JSON.parse(calls[1].init.body);
    expect(createPayload.metadata).toEqual({ motivo: "Motivo mínimo" });
  });

  it("un valor number inválido ('abc') se omite en vez de enviar NaN", async () => {
    const calls = installTemplateThenCreateFetch();
    const fd = new FormData();
    fd.set("name", "Expediente con monto inválido");
    fd.set("trd_serie_id", SERIE_ID);
    fd.set("meta_monto_contrato", "no-es-un-numero");

    try {
      await actions.create({
        request: { formData: async () => fd },
        cookies: { get: () => "tok" },
        locals: { accessToken: "tok", user: { tenant_slug: "icetex" } },
      });
    } catch {
      // redirect esperado
    }

    const createPayload = JSON.parse(calls[1].init.body);
    expect(createPayload.metadata.monto_contrato).toBeUndefined();
    expect(JSON.stringify(createPayload)).not.toContain("NaN");
  });

  it("sin serie TRD elegida: no se llama al endpoint de plantillas, metadata queda {}", async () => {
    const calls = [];
    globalThis.fetch = vi.fn(async (input, init) => {
      calls.push({ input, init });
      return { ok: true, status: 201, json: async () => ({ id: "exp-sin-serie" }) };
    });

    const fd = new FormData();
    fd.set("name", "Expediente sin serie");

    try {
      await actions.create({
        request: { formData: async () => fd },
        cookies: { get: () => "tok" },
        locals: { accessToken: "tok", user: { tenant_slug: "icetex" } },
      });
    } catch {
      // redirect esperado
    }

    expect(calls).toHaveLength(1); // solo la creación, ninguna llamada a plantillas
    const createPayload = JSON.parse(calls[0].init.body);
    expect(createPayload.metadata).toEqual({});
  });

  it("serie sin plantilla activa (lista vacía): metadata queda {} sin romper la creación", async () => {
    const calls = installTemplateThenCreateFetch({ templateBody: [] });
    const fd = new FormData();
    fd.set("name", "Expediente con serie sin plantilla");
    fd.set("trd_serie_id", SERIE_ID);
    fd.set("meta_motivo", "no debería colarse, no hay schema");

    try {
      await actions.create({
        request: { formData: async () => fd },
        cookies: { get: () => "tok" },
        locals: { accessToken: "tok", user: { tenant_slug: "icetex" } },
      });
    } catch {
      // redirect esperado
    }

    const createPayload = JSON.parse(calls[1].init.body);
    expect(createPayload.metadata).toEqual({});
  });

  it("5xx al traer la plantilla: 503 connection_error, NO se descartan name/description ya escritos", async () => {
    globalThis.fetch = vi.fn(async () => ({ ok: false, status: 500, json: async () => ({}) }));

    const fd = new FormData();
    fd.set("name", "Expediente con plantillas caídas");
    fd.set("description", "No se debe perder esta descripción");
    fd.set("trd_serie_id", SERIE_ID);

    const result = await actions.create({
      request: { formData: async () => fd },
      cookies: { get: () => "tok" },
      locals: { accessToken: "tok", user: { tenant_slug: "icetex" } },
    });

    expect(result.status).toBe(503);
    expect(result.data.error).toBe("connection_error");
    expect(result.data.values.name).toBe("Expediente con plantillas caídas");
    expect(result.data.values.description).toBe("No se debe perder esta descripción");
  });

  it("fallo de red al traer la plantilla: 503 connection_error", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("network down");
    });

    const fd = new FormData();
    fd.set("name", "Expediente con red caída");
    fd.set("trd_serie_id", SERIE_ID);

    const result = await actions.create({
      request: { formData: async () => fd },
      cookies: { get: () => "tok" },
      locals: { accessToken: "tok", user: { tenant_slug: "icetex" } },
    });

    expect(result.status).toBe(503);
    expect(result.data.error).toBe("connection_error");
  });

  it("422 metadata_validation_failed tras construir metadata: repuebla `values.meta` con los raw strings", async () => {
    const calls = [];
    globalThis.fetch = vi.fn(async (input, init) => {
      calls.push({ input, init });
      if (calls.length === 1) {
        return { ok: true, status: 200, json: async () => [{ ...SCHEMA, activo: true }] };
      }
      return {
        ok: false,
        status: 422,
        json: async () => ({
          error: "metadata_validation_failed",
          detail: "'monto_contrato' is a required property",
        }),
      };
    });

    const fd = new FormData();
    fd.set("name", "Expediente que falla validación");
    fd.set("trd_serie_id", SERIE_ID);
    fd.set("meta_motivo", "Motivo escrito por el usuario");

    const result = await actions.create({
      request: { formData: async () => fd },
      cookies: { get: () => "tok" },
      locals: { accessToken: "tok", user: { tenant_slug: "icetex" } },
    });

    expect(result.status).toBe(422);
    expect(result.data.values.meta).toEqual({ motivo: "Motivo escrito por el usuario" });
  });
});
