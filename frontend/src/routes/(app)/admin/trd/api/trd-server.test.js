// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
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
          retention_years: 5,
          total_retention: 10,
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
      retention_years: 5,
      total_retention: 10,
      disposition: "CT",
      parent_id: null,
      archivo_gestion_years: null,
      archivo_central_years: null,
      pdfa_profile: "2a",
    });
    expect(capturedHeaders["X-Tenant-Slug"]).toBe("icetex");
    expect(capturedHeaders["X-User-Id"]).toBe(SESSION_LOCALS.user.sub);
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
});
