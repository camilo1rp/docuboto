// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * trd-server.test.js — Proxies /admin/trd/api[, /[id]] (D-05, E04).
 *
 * Cubre: guard 401, allowlist anti mass-assignment de POST (nunca id/created_at),
 * validación de disposition/pdfa_profile/parent_id, encodeURIComponent del [id]
 * en PATCH, y propagación de status de error del gateway.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { GET as listSeries, POST as createSerie } from "./+server.js";
import { PATCH as updateSerie } from "./[id]/+server.js";

const SERIE_ID = "44444444-4444-4444-4444-444444444444";
const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("GET /admin/trd/api", () => {
  it("401 sin sesión, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await listSeries({ locals: {}, url: new URL("http://x/") });
    expect(res.status).toBe(401);
  });

  it("propaga X-Total-Count y usa size=200 por defecto (árbol completo)", async () => {
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input) => {
      capturedUrl = String(input);
      return new Response(JSON.stringify([]), { status: 200, headers: { "X-Total-Count": "3" } });
    });
    const res = await listSeries({ locals: SESSION_LOCALS, url: new URL("http://x/") });
    expect(res.status).toBe(200);
    expect(res.headers.get("X-Total-Count")).toBe("3");
    expect(capturedUrl).toContain("size=200");
  });

  it("propaga status de error del gateway", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ error: "forbidden", status: 403 }), { status: 403 }),
    );
    const res = await listSeries({ locals: SESSION_LOCALS, url: new URL("http://x/") });
    expect(res.status).toBe(403);
  });
});

describe("POST /admin/trd/api", () => {
  it("401 sin sesión", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await createSerie({
      locals: {},
      request: { json: async () => ({ code: "A", name: "B" }) },
    });
    expect(res.status).toBe(401);
  });

  it("sin code: 400, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await createSerie({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ name: "B" }) },
    });
    expect(res.status).toBe(400);
  });

  it("disposition inválida: 400, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await createSerie({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ code: "A", name: "B", disposition: "DROP TABLE" }) },
    });
    expect(res.status).toBe(400);
  });

  it("parent_id no-UUID: 400, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await createSerie({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ code: "A", name: "B", parent_id: "no-es-uuid" }) },
    });
    expect(res.status).toBe(400);
  });

  it("allowlist: solo campos de TrdSerieCreate viajan; id/created_at/is_active NUNCA (mass-assignment)", async () => {
    let capturedBody;
    let capturedHeaders;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedBody = JSON.parse(init.body);
      capturedHeaders = init.headers;
      return new Response(JSON.stringify({ id: "s-1" }), { status: 201 });
    });

    const res = await createSerie({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({
          code: "OF",
          name: "Oficios",
          description: "  ",
          retention_years: 5, // legado: debe descartarse (422 en el backend)
          total_retention: 10, // legado: ídem
          archivo_gestion_years: 2,
          archivo_central_years: 0,
          dependencias_productoras: [3, "4", 3],
          disposition: "CT",
          parent_id: null,
          pdfa_profile: "2a",
          // Campos ajenos que un cliente malicioso podría intentar colar:
          id: "otro-id",
          is_active: false,
          created_at: "2020-01-01T00:00:00Z",
          tenant_slug: "otro-tenant",
        }),
      },
    });

    expect(res.status).toBe(201);
    expect(capturedBody).toEqual({
      code: "OF",
      name: "Oficios",
      description: null,
      disposition: "CT",
      parent_id: null,
      archivo_gestion_years: 2,
      archivo_central_years: 0,
      pdfa_profile: "2a",
      dependencias_productoras: [3, 4],
    });
    expect(capturedBody.retention_years).toBeUndefined();
    expect(capturedBody.total_retention).toBeUndefined();
    expect(capturedHeaders["X-Tenant-Slug"]).toBe("icetex");
    expect(capturedHeaders["X-User-Id"]).toBe(SESSION_LOCALS.user.sub);
  });
});

describe("POST /admin/trd/api — fases y productoras (C-4)", () => {
  const base = { code: "OF", name: "Oficios", archivo_gestion_years: 2, archivo_central_years: 3 };
  const llamar = (body) =>
    createSerie({ locals: SESSION_LOCALS, request: { json: async () => body } });
  const sinGateway = () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
  };

  it("control positivo: fases válidas llegan al gateway (0 + 1 también)", async () => {
    globalThis.fetch = vi.fn(
      async () => new Response(JSON.stringify({ id: "s" }), { status: 201 }),
    );
    expect((await llamar(base)).status).toBe(201);
    expect(
      (await llamar({ ...base, archivo_gestion_years: 0, archivo_central_years: 1 })).status,
    ).toBe(201);
  });

  it("sin fases: 400 y sin llamar al gateway (no hay valor por omisión)", async () => {
    sinGateway();
    expect((await llamar({ code: "OF", name: "Oficios" })).status).toBe(400);
    expect((await llamar({ ...base, archivo_central_years: undefined })).status).toBe(400);
    expect((await llamar({ ...base, archivo_gestion_years: "" })).status).toBe(400);
  });

  it("fases negativas o no enteras: 400", async () => {
    sinGateway();
    expect((await llamar({ ...base, archivo_gestion_years: -1 })).status).toBe(400);
    expect((await llamar({ ...base, archivo_central_years: 1.5 })).status).toBe(400);
  });

  it("gestión + central = 0: 400 (retención mínima de 1 año)", async () => {
    sinGateway();
    const res = await llamar({ ...base, archivo_gestion_years: 0, archivo_central_years: 0 });
    expect(res.status).toBe(400);
    expect((await res.json()).detail).toMatch(/al menos 1 año/);
  });

  it("dependencias_productoras inválidas: 400", async () => {
    sinGateway();
    expect((await llamar({ ...base, dependencias_productoras: "3" })).status).toBe(400);
    expect((await llamar({ ...base, dependencias_productoras: [0] })).status).toBe(400);
    expect((await llamar({ ...base, dependencias_productoras: ["x"] })).status).toBe(400);
  });

  it("propaga el error de contrato del backend tal cual (422 dependencia_desconocida)", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ error: "dependencia_desconocida", status: 422 }), {
          status: 422,
        }),
    );
    const res = await llamar({ ...base, dependencias_productoras: [99] });
    expect(res.status).toBe(422);
    expect((await res.json()).error).toBe("dependencia_desconocida");
  });
});

describe("GET /admin/trd/api — frontera de lectura (C-4)", () => {
  it("reenvía incluir_en_tramite=true solo cuando es exactamente 'true'", async () => {
    const urls = [];
    globalThis.fetch = vi.fn(async (input) => {
      urls.push(String(input));
      return new Response("[]", { status: 200 });
    });
    await listSeries({ locals: SESSION_LOCALS, url: new URL("http://x/?incluir_en_tramite=true") });
    await listSeries({ locals: SESSION_LOCALS, url: new URL("http://x/?incluir_en_tramite=1") });
    expect(urls[0]).toContain("incluir_en_tramite=true");
    expect(urls[1]).not.toContain("incluir_en_tramite");
  });
});

describe("PATCH /admin/trd/api/[id]", () => {
  it("401 sin sesión", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await updateSerie({
      locals: {},
      params: { id: SERIE_ID },
      request: { json: async () => ({}) },
    });
    expect(res.status).toBe(401);
  });

  it("id inválido: 400, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await updateSerie({
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
      return new Response(JSON.stringify({ id: SERIE_ID, name: "Nuevo nombre" }), { status: 200 });
    });

    const res = await updateSerie({
      locals: SESSION_LOCALS,
      params: { id: SERIE_ID },
      request: { json: async () => ({ name: "Nuevo nombre", code: "IGNORAR" }) },
    });

    expect(res.status).toBe(200);
    expect(capturedUrl).toContain(`/trd/${SERIE_ID}`);
    expect(capturedBody).toEqual({ name: "Nuevo nombre" });
    expect(capturedBody.code).toBeUndefined();
  });

  it("fases y productoras viajan; retention_years/total_retention nunca", async () => {
    let capturedBody;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ id: SERIE_ID }), { status: 200 });
    });
    const res = await updateSerie({
      locals: SESSION_LOCALS,
      params: { id: SERIE_ID },
      request: {
        json: async () => ({
          archivo_gestion_years: 4,
          dependencias_productoras: [7],
          retention_years: 9,
          total_retention: 20,
        }),
      },
    });
    expect(res.status).toBe(200);
    expect(capturedBody).toEqual({ archivo_gestion_years: 4, dependencias_productoras: [7] });
  });

  it("fase inválida o suma 0 en el mismo PATCH: 400 sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const llamar = (body) =>
      updateSerie({
        locals: SESSION_LOCALS,
        params: { id: SERIE_ID },
        request: { json: async () => body },
      });
    expect((await llamar({ archivo_central_years: -2 })).status).toBe(400);
    expect((await llamar({ archivo_gestion_years: 0, archivo_central_years: 0 })).status).toBe(400);
    expect((await llamar({ dependencias_productoras: [-1] })).status).toBe(400);
  });
});
