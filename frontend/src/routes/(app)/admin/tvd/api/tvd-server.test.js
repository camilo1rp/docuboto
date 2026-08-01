// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * tvd-server.test.js — Proxies /admin/tvd/api[, /[id]] (D-05, ADR-026).
 *
 * Cubre: guard 401, allowlist anti mass-assignment de POST (nunca id/
 * created_at/estado/tipo_instrumento), validación de fechas extremas/
 * fondo_nombre/justificacion_valoracion obligatorios, disposition/
 * pdfa_profile/parent_id, encodeURIComponent del [id] en PATCH, append-only
 * de PATCH (allowlist parcial) y propagación de status de error del gateway.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { GET as listAgrupaciones, POST as createAgrupacion } from "./+server.js";
import { PATCH as updateAgrupacion } from "./[id]/+server.js";

const AGRUPACION_ID = "55555555-5555-5555-5555-555555555555";
const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("GET /admin/tvd/api", () => {
  it("401 sin sesión, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await listAgrupaciones({ locals: {}, url: new URL("http://x/") });
    expect(res.status).toBe(401);
  });

  it("propaga X-Total-Count y usa size=200 por defecto (árbol completo)", async () => {
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input) => {
      capturedUrl = String(input);
      return new Response(JSON.stringify([]), { status: 200, headers: { "X-Total-Count": "2" } });
    });
    const res = await listAgrupaciones({ locals: SESSION_LOCALS, url: new URL("http://x/") });
    expect(res.status).toBe(200);
    expect(res.headers.get("X-Total-Count")).toBe("2");
    expect(capturedUrl).toContain("/tvd?");
    expect(capturedUrl).toContain("size=200");
  });

  it("propaga status de error del gateway", async () => {
    globalThis.fetch = vi.fn(
      async () => new Response(JSON.stringify({ error: "forbidden", status: 403 }), { status: 403 }),
    );
    const res = await listAgrupaciones({ locals: SESSION_LOCALS, url: new URL("http://x/") });
    expect(res.status).toBe(403);
  });
});

describe("POST /admin/tvd/api", () => {
  const validBody = () => ({
    code: "FA-01",
    name: "Fondo acumulado nómina",
    fondo_nombre: "Instituto liquidado en 2003",
    fecha_extrema_inicial: "1985-01-01",
    fecha_extrema_final: "1998-12-31",
    justificacion_valoracion: "Valoración secundaria: sin valor administrativo vigente.",
  });

  it("401 sin sesión", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await createAgrupacion({ locals: {}, request: { json: async () => validBody() } });
    expect(res.status).toBe(401);
  });

  it("sin code: 400, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const { code, ...rest } = validBody();
    const res = await createAgrupacion({ locals: SESSION_LOCALS, request: { json: async () => rest } });
    expect(res.status).toBe(400);
  });

  it("sin fondo_nombre: 400 (obligatorio en TVD, no en TRD)", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const { fondo_nombre, ...rest } = validBody();
    const res = await createAgrupacion({ locals: SESSION_LOCALS, request: { json: async () => rest } });
    expect(res.status).toBe(400);
  });

  it("sin justificacion_valoracion: 400 (es el objeto del instrumento, ADR-026 D5)", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const { justificacion_valoracion, ...rest } = validBody();
    const res = await createAgrupacion({ locals: SESSION_LOCALS, request: { json: async () => rest } });
    expect(res.status).toBe(400);
  });

  it("sin fechas extremas: 400", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const { fecha_extrema_inicial, ...rest } = validBody();
    const res = await createAgrupacion({ locals: SESSION_LOCALS, request: { json: async () => rest } });
    expect(res.status).toBe(400);
  });

  it("fecha_extrema_final anterior a fecha_extrema_inicial: 400, sin llamar al gateway (espejo del CHECK del backend)", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await createAgrupacion({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ ...validBody(), fecha_extrema_inicial: "2000-01-01", fecha_extrema_final: "1999-01-01" }) },
    });
    expect(res.status).toBe(400);
  });

  it("disposition inválida: 400, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await createAgrupacion({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ ...validBody(), disposition: "DROP TABLE" }) },
    });
    expect(res.status).toBe(400);
  });

  it("parent_id no-UUID: 400, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await createAgrupacion({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ ...validBody(), parent_id: "no-es-uuid" }) },
    });
    expect(res.status).toBe(400);
  });

  it("allowlist: solo campos de TvdAgrupacionCreate viajan; id/estado/tipo_instrumento/archivo_gestion_years NUNCA (mass-assignment)", async () => {
    let capturedBody;
    let capturedHeaders;
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedBody = JSON.parse(init.body);
      capturedHeaders = init.headers;
      return new Response(JSON.stringify({ id: "tvd-1" }), { status: 201 });
    });

    const res = await createAgrupacion({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({
          ...validBody(),
          description: "  ",
          parent_id: null,
          archivo_central_years: 20,
          total_retention: 30,
          disposition: "CT",
          pdfa_profile: "2a",
          // Campos ajenos / prohibidos por ADR-026 que un cliente malicioso
          // podría intentar colar:
          id: "otro-id",
          estado: "convalidada",
          tipo_instrumento: "TRD",
          archivo_gestion_years: 5,
          is_active: false,
          created_at: "2020-01-01T00:00:00Z",
        }),
      },
    });

    expect(res.status).toBe(201);
    expect(capturedUrl).toContain("/tvd");
    expect(capturedBody).toEqual({
      code: "FA-01",
      name: "Fondo acumulado nómina",
      description: null,
      parent_id: null,
      fondo_nombre: "Instituto liquidado en 2003",
      fecha_extrema_inicial: "1985-01-01",
      fecha_extrema_final: "1998-12-31",
      justificacion_valoracion: "Valoración secundaria: sin valor administrativo vigente.",
      archivo_central_years: 20,
      total_retention: 30,
      disposition: "CT",
      pdfa_profile: "2a",
    });
    expect(capturedBody.archivo_gestion_years).toBeUndefined();
    expect(capturedBody.estado).toBeUndefined();
    expect(capturedBody.tipo_instrumento).toBeUndefined();
    expect(capturedHeaders["X-Tenant-Slug"]).toBe("icetex");
    expect(capturedHeaders["X-User-Id"]).toBe(SESSION_LOCALS.user.sub);
  });
});

describe("PATCH /admin/tvd/api/[id]", () => {
  it("401 sin sesión", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await updateAgrupacion({
      locals: {},
      params: { id: AGRUPACION_ID },
      request: { json: async () => ({}) },
    });
    expect(res.status).toBe(401);
  });

  it("id inválido: 400, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await updateAgrupacion({
      locals: SESSION_LOCALS,
      params: { id: "no-es-uuid" },
      request: { json: async () => ({}) },
    });
    expect(res.status).toBe(400);
  });

  it("escapa el id en la URL del gateway y envía allowlist parcial (exclude_unset)", async () => {
    let capturedUrl;
    let capturedBody;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ id: AGRUPACION_ID, pdfa_profile: "2a" }), { status: 200 });
    });

    const res = await updateAgrupacion({
      locals: SESSION_LOCALS,
      params: { id: AGRUPACION_ID },
      request: { json: async () => ({ pdfa_profile: "2a", code: "IGNORAR" }) },
    });

    expect(res.status).toBe(200);
    expect(capturedUrl).toContain(`/tvd/${AGRUPACION_ID}`);
    expect(capturedBody).toEqual({ pdfa_profile: "2a" });
    expect(capturedBody.code).toBeUndefined();
  });

  it("fecha_extrema_final anterior a fecha_extrema_inicial: 400, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await updateAgrupacion({
      locals: SESSION_LOCALS,
      params: { id: AGRUPACION_ID },
      request: {
        json: async () => ({ fecha_extrema_inicial: "2000-01-01", fecha_extrema_final: "1999-01-01" }),
      },
    });
    expect(res.status).toBe(400);
  });
});
