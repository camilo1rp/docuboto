// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * reglas-server.test.js — Proxies BFF de /admin/flujos/reglas (D-05).
 *
 * Cubre: guard 401, allowlist campo a campo (anti mass-assignment), coerción
 * de `conditions[]` contra el enum cerrado del backend, validación de id
 * (UUID + encodeURIComponent), y el reenvío correcto de Authorization/X-Tenant-Slug.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { GET as listGet, POST as createPost } from "./+server.js";
import { GET as detailGet, PATCH as detailPatch, DELETE as detailDelete } from "./[id]/+server.js";

const RULE_ID = "11111111-1111-1111-1111-111111111111";
const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};
const url = (path) => new URL(`http://localhost${path}`);

afterEach(() => {
  vi.restoreAllMocks();
});

describe("reglas/api GET (lista) — guard 401", () => {
  it("sin locals.accessToken: 401 unauthenticated, sin llamar al gateway", async () => {
    const fetchMock = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    globalThis.fetch = fetchMock;
    const res = await listGet({ locals: {}, url: url("/admin/flujos/reglas/api") });
    expect(res.status).toBe(401);
    expect((await res.json()).error).toBe("unauthenticated");
  });

  it("reenvía Authorization/X-Tenant-Slug y pasa page/size/active_only al gateway", async () => {
    let capturedUrl;
    let capturedHeaders;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedHeaders = init?.headers;
      return new Response(JSON.stringify([]), { status: 200 });
    });
    const res = await listGet({
      locals: SESSION_LOCALS,
      url: url("/admin/flujos/reglas/api?active_only=true"),
    });
    expect(res.status).toBe(200);
    expect(capturedUrl).toContain("/workflow/rules?");
    expect(capturedUrl).toContain("active_only=true");
    expect(capturedUrl).toContain("size=200");
    expect(capturedHeaders.Authorization).toBe("Bearer tok-abc");
    expect(capturedHeaders["X-Tenant-Slug"]).toBe("icetex");
  });
});

describe("reglas/api POST (crear) — validación y allowlist", () => {
  it("sin locals.accessToken: 401, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await createPost({
      locals: {},
      request: { json: async () => ({ name: "x", assign_to_dept_code: 7 }) },
    });
    expect(res.status).toBe(401);
  });

  it("sin name: 422 validation_error, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin name");
    });
    const res = await createPost({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ name: "  ", assign_to_dept_code: 7 }) },
    });
    expect(res.status).toBe(422);
    expect((await res.json()).error).toBe("validation_error");
  });

  it("sin assign_to_dept_code: 422 validation_error, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin assign_to_dept_code");
    });
    const res = await createPost({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ name: "Regla X", assign_to_dept_code: "" }) },
    });
    expect(res.status).toBe(422);
    expect((await res.json()).error).toBe("validation_error");
  });

  it("allowlist: solo reenvía los campos del schema, descarta condiciones fuera del enum y coerciona value", async () => {
    let capturedUrl;
    let capturedBody;
    let capturedHeaders;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedBody = JSON.parse(init.body);
      capturedHeaders = init.headers;
      return new Response(JSON.stringify({ id: "r-1", ...capturedBody }), { status: 201 });
    });

    const res = await createPost({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({
          name: "  Circulares a TIC  ",
          description: "  ",
          is_active: false,
          priority: 5,
          match_mode: "any",
          assign_to_dept_code: "7",
          assign_notes: "auto",
          conditions: [
            { field: "doc_type", op: "eq", value: "E" },
            { field: "pages", op: "gte", value: "10" },
            { field: "campo_inventado", op: "eq", value: "x" }, // fuera de catálogo
          ],
          // Campos ajenos que un cliente malicioso podría intentar colar:
          created_by: "otro-usuario-id",
          id: "id-forzado",
        }),
      },
    });

    expect(res.status).toBe(201);
    expect(capturedUrl).toContain("/workflow/rules");
    expect(capturedBody).toEqual({
      name: "Circulares a TIC",
      description: null,
      is_active: false,
      priority: 5,
      match_mode: "any",
      assign_to_dept_code: 7,
      assign_notes: "auto",
      conditions: [
        { field: "doc_type", op: "eq", value: "E" },
        { field: "pages", op: "gte", value: 10 },
      ],
    });
    expect(capturedBody.created_by).toBeUndefined();
    expect(capturedBody.id).toBeUndefined();
    expect(capturedHeaders.Authorization).toBe("Bearer tok-abc");
    expect(capturedHeaders["X-Tenant-Slug"]).toBe("icetex");
    expect(capturedHeaders["X-User-Id"]).toBe(SESSION_LOCALS.user.sub);
  });

  it("priority omitida: usa el default 100", async () => {
    let capturedBody;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ id: "r-1" }), { status: 201 });
    });
    await createPost({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({ name: "X", assign_to_dept_code: 7 }),
      },
    });
    expect(capturedBody.priority).toBe(100);
  });

  it.each([[0], [1000], [-5], ["no-numero"]])(
    "priority=%s fuera de rango: 422 validation_error, sin llamar al gateway",
    async (bad) => {
      globalThis.fetch = vi.fn(() => {
        throw new Error("fetch no debió invocarse con priority fuera de rango");
      });
      const res = await createPost({
        locals: SESSION_LOCALS,
        request: {
          json: async () => ({ name: "X", assign_to_dept_code: 7, priority: bad }),
        },
      });
      expect(res.status).toBe(422);
      expect((await res.json()).error).toBe("validation_error");
    },
  );

  it("fallo de red del gateway: 503 connection_error", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new Error("network down");
    });
    const res = await createPost({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ name: "X", assign_to_dept_code: 7 }) },
    });
    expect(res.status).toBe(503);
    expect((await res.json()).error).toBe("connection_error");
  });
});

describe("reglas/api/[id] — allowlist de id (UUID) + encodeURIComponent", () => {
  it.each([["no-es-un-uuid"], ["'; DROP TABLE workflow_rules; --"], [""]])(
    "GET id=%s inválido: 400 validation_error, sin llamar al gateway",
    async (bad) => {
      globalThis.fetch = vi.fn(() => {
        throw new Error("fetch no debió invocarse con id inválido");
      });
      const res = await detailGet({ locals: SESSION_LOCALS, params: { id: bad } });
      expect(res.status).toBe(400);
      expect((await res.json()).error).toBe("validation_error");
    },
  );

  it("GET id válido: encodeURIComponent en la URL hacia el gateway", async () => {
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input) => {
      capturedUrl = String(input);
      return new Response(JSON.stringify({ id: RULE_ID }), { status: 200 });
    });
    const res = await detailGet({ locals: SESSION_LOCALS, params: { id: RULE_ID } });
    expect(res.status).toBe(200);
    expect(capturedUrl).toContain(`/workflow/rules/${encodeURIComponent(RULE_ID)}`);
  });

  it("PATCH: sin locals.accessToken: 401, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await detailPatch({
      locals: {},
      params: { id: RULE_ID },
      request: { json: async () => ({ name: "x" }) },
    });
    expect(res.status).toBe(401);
  });

  it("PATCH: edición parcial — solo reenvía las claves presentes en el body del cliente", async () => {
    let capturedBody;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ id: RULE_ID }), { status: 200 });
    });
    await detailPatch({
      locals: SESSION_LOCALS,
      params: { id: RULE_ID },
      request: { json: async () => ({ is_active: false }) },
    });
    expect(capturedBody).toEqual({ is_active: false });
  });

  it("PATCH: priority fuera de rango → 422, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse con priority fuera de rango");
    });
    const res = await detailPatch({
      locals: SESSION_LOCALS,
      params: { id: RULE_ID },
      request: { json: async () => ({ priority: 1500 }) },
    });
    expect(res.status).toBe(422);
    expect((await res.json()).error).toBe("validation_error");
  });

  it("PATCH: name presente pero vacío → 422, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse con name vacío");
    });
    const res = await detailPatch({
      locals: SESSION_LOCALS,
      params: { id: RULE_ID },
      request: { json: async () => ({ name: "   " }) },
    });
    expect(res.status).toBe(422);
  });

  it("DELETE: id inválido → 400, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse con id inválido");
    });
    const res = await detailDelete({ locals: SESSION_LOCALS, params: { id: "bad" } });
    expect(res.status).toBe(400);
  });

  it("DELETE: 204 del gateway se reenvía tal cual", async () => {
    globalThis.fetch = vi.fn(async () => new Response(null, { status: 204 }));
    const res = await detailDelete({ locals: SESSION_LOCALS, params: { id: RULE_ID } });
    expect(res.status).toBe(204);
  });
});
