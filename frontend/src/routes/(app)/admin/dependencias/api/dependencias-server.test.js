// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * dependencias-server.test.js — Proxies BFF de /admin/dependencias (D-05).
 *
 * Cubre: guard 401, allowlist campo a campo (anti mass-assignment), edición
 * PARCIAL real (solo las claves presentes en el body del cliente), UUID +
 * encodeURIComponent en el id de ruta.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { GET as listGet, POST as createPost } from "./+server.js";
import { GET as treeGet } from "./tree/+server.js";
import { PATCH as patchOne } from "./[id]/+server.js";

const DEP_ID = "11111111-1111-1111-1111-111111111111";
const PARENT_ID = "22222222-2222-2222-2222-222222222222";
const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};
const url = (path) => new URL(`http://localhost${path}`);

afterEach(() => {
  vi.restoreAllMocks();
});

function noFetchExpected(msg) {
  return vi.fn(() => {
    throw new Error(msg ?? "fetch no debió invocarse");
  });
}

// ---------------------------------------------------------------------------
// GET (lista plana) + POST (crear)
// ---------------------------------------------------------------------------
describe("dependencias/api GET (lista plana)", () => {
  it("sin locals.accessToken: 401, sin llamar al gateway", async () => {
    globalThis.fetch = noFetchExpected();
    const res = await listGet({ locals: {}, url: url("/admin/dependencias/api") });
    expect(res.status).toBe(401);
  });

  it("reenvía activo/parent_id válidos, descarta valores fuera de catálogo", async () => {
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input) => {
      capturedUrl = String(input);
      return new Response(JSON.stringify([]), { status: 200 });
    });
    const res = await listGet({
      locals: SESSION_LOCALS,
      url: url(`/admin/dependencias/api?activo=true&parent_id=${PARENT_ID}`),
    });
    expect(res.status).toBe(200);
    expect(capturedUrl).toContain("/dependencias?");
    expect(capturedUrl).toContain("activo=true");
    expect(capturedUrl).toContain(`parent_id=${PARENT_ID}`);
  });

  it("activo con valor fuera de catálogo (ni true ni false) se descarta silenciosamente", async () => {
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input) => {
      capturedUrl = String(input);
      return new Response(JSON.stringify([]), { status: 200 });
    });
    await listGet({
      locals: SESSION_LOCALS,
      url: url("/admin/dependencias/api?activo=maybe"),
    });
    expect(capturedUrl).not.toContain("activo=");
  });
});

describe("dependencias/api POST (crear)", () => {
  it("sin locals.accessToken: 401, sin llamar al gateway", async () => {
    globalThis.fetch = noFetchExpected();
    const res = await createPost({
      locals: {},
      request: { json: async () => ({ codigo: 100, sigla: "TIC", nombre: "Tecnología" }) },
    });
    expect(res.status).toBe(401);
  });

  it.each([[0], [1000000], [-5], ["no-numero"]])(
    "codigo=%s fuera de rango: 400 validation_error, sin llamar al gateway",
    async (bad) => {
      globalThis.fetch = noFetchExpected();
      const res = await createPost({
        locals: SESSION_LOCALS,
        request: { json: async () => ({ codigo: bad, sigla: "TIC", nombre: "Tecnología" }) },
      });
      expect(res.status).toBe(400);
      expect((await res.json()).error).toBe("validation_error");
    },
  );

  it("sin sigla: 400 validation_error, sin llamar al gateway", async () => {
    globalThis.fetch = noFetchExpected();
    const res = await createPost({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ codigo: 100, sigla: "   ", nombre: "Tecnología" }) },
    });
    expect(res.status).toBe(400);
  });

  it("sin nombre: 400 validation_error, sin llamar al gateway", async () => {
    globalThis.fetch = noFetchExpected();
    const res = await createPost({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ codigo: 100, sigla: "TIC", nombre: "" }) },
    });
    expect(res.status).toBe(400);
  });

  it("parent_id inválido (no UUID): 400 validation_error, sin llamar al gateway", async () => {
    globalThis.fetch = noFetchExpected();
    const res = await createPost({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({
          codigo: 100,
          sigla: "TIC",
          nombre: "Tecnología",
          parent_id: "'; DROP TABLE dependencias; --",
        }),
      },
    });
    expect(res.status).toBe(400);
  });

  it("allowlist: solo reenvía los campos del schema, descarta campos ajenos", async () => {
    let capturedBody;
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ id: "d-1", ...capturedBody }), { status: 201 });
    });
    const res = await createPost({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({
          codigo: 105,
          sigla: "  TIC  ",
          nombre: "  Tecnología  ",
          parent_id: PARENT_ID,
          depe_rad_tp2: true,
          activo: false,
          id: "id-forzado",
          created_at: "hoy",
        }),
      },
    });
    expect(res.status).toBe(201);
    expect(capturedUrl).toContain("/dependencias");
    expect(capturedBody).toEqual({
      codigo: 105,
      sigla: "TIC",
      nombre: "Tecnología",
      parent_id: PARENT_ID,
      depe_rad_tp2: true,
      activo: false,
    });
    expect(capturedBody.id).toBeUndefined();
    expect(capturedBody.created_at).toBeUndefined();
  });

  it("parent_id/depe_rad_tp2/activo omitidos: usan sus defaults (raíz, false, true)", async () => {
    let capturedBody;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ id: "d-1" }), { status: 201 });
    });
    await createPost({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ codigo: 100, sigla: "TIC", nombre: "Tecnología" }) },
    });
    expect(capturedBody.parent_id).toBeNull();
    expect(capturedBody.depe_rad_tp2).toBe(false);
    expect(capturedBody.activo).toBe(true);
  });

  it("fallo de red del gateway: 503 connection_error", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new Error("network down");
    });
    const res = await createPost({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ codigo: 100, sigla: "TIC", nombre: "Tecnología" }) },
    });
    expect(res.status).toBe(503);
    expect((await res.json()).error).toBe("connection_error");
  });
});

// ---------------------------------------------------------------------------
// GET /tree
// ---------------------------------------------------------------------------
describe("dependencias/api/tree GET", () => {
  it("sin locals.accessToken: 401, sin llamar al gateway", async () => {
    globalThis.fetch = noFetchExpected();
    const res = await treeGet({ locals: {}, url: url("/admin/dependencias/api/tree") });
    expect(res.status).toBe(401);
  });

  it("reenvía Authorization/X-Tenant-Slug hacia /dependencias/tree", async () => {
    let capturedUrl;
    let capturedHeaders;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedHeaders = init?.headers;
      return new Response(JSON.stringify([]), { status: 200 });
    });
    const res = await treeGet({ locals: SESSION_LOCALS, url: url("/admin/dependencias/api/tree") });
    expect(res.status).toBe(200);
    expect(capturedUrl).toContain("/dependencias/tree");
    expect(capturedHeaders.Authorization).toBe("Bearer tok-abc");
    expect(capturedHeaders["X-Tenant-Slug"]).toBe("icetex");
  });
});

// ---------------------------------------------------------------------------
// PATCH /[id] — edición parcial
// ---------------------------------------------------------------------------
describe("dependencias/api/[id] PATCH", () => {
  it("sin locals.accessToken: 401, sin llamar al gateway", async () => {
    globalThis.fetch = noFetchExpected();
    const res = await patchOne({
      locals: {},
      params: { id: DEP_ID },
      request: { json: async () => ({ nombre: "x" }) },
    });
    expect(res.status).toBe(401);
  });

  it.each([["no-es-un-uuid"], ["'; DROP TABLE dependencias; --"], [""]])(
    "id=%s inválido: 400 validation_error, sin llamar al gateway",
    async (bad) => {
      globalThis.fetch = noFetchExpected();
      const res = await patchOne({
        locals: SESSION_LOCALS,
        params: { id: bad },
        request: { json: async () => ({ nombre: "x" }) },
      });
      expect(res.status).toBe(400);
      expect((await res.json()).error).toBe("validation_error");
    },
  );

  it("edición parcial: solo reenvía las claves presentes en el body del cliente", async () => {
    let capturedBody;
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ id: DEP_ID }), { status: 200 });
    });
    const res = await patchOne({
      locals: SESSION_LOCALS,
      params: { id: DEP_ID },
      request: { json: async () => ({ activo: false }) },
    });
    expect(res.status).toBe(200);
    expect(capturedBody).toEqual({ activo: false });
    expect(capturedUrl).toContain(`/dependencias/${encodeURIComponent(DEP_ID)}`);
  });

  it("nombre presente pero vacío → 400, sin llamar al gateway", async () => {
    globalThis.fetch = noFetchExpected();
    const res = await patchOne({
      locals: SESSION_LOCALS,
      params: { id: DEP_ID },
      request: { json: async () => ({ nombre: "   " }) },
    });
    expect(res.status).toBe(400);
  });

  it("codigo presente (incluso el mismo) → 422 dependencia_codigo_inmutable, sin llamar al gateway (ADR-032)", async () => {
    globalThis.fetch = noFetchExpected();
    for (const codigo of [0, 105, 999]) {
      const res = await patchOne({
        locals: SESSION_LOCALS,
        params: { id: DEP_ID },
        request: { json: async () => ({ codigo, nombre: "Otro" }) },
      });
      expect(res.status).toBe(422);
      expect((await res.json()).error).toBe("dependencia_codigo_inmutable");
    }
  });

  it("parent_id presente e inválido → 400, sin llamar al gateway", async () => {
    globalThis.fetch = noFetchExpected();
    const res = await patchOne({
      locals: SESSION_LOCALS,
      params: { id: DEP_ID },
      request: { json: async () => ({ parent_id: "no-es-uuid" }) },
    });
    expect(res.status).toBe(400);
  });

  it("parent_id: null explícito es válido (desanidar a raíz)", async () => {
    let capturedBody;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ id: DEP_ID }), { status: 200 });
    });
    const res = await patchOne({
      locals: SESSION_LOCALS,
      params: { id: DEP_ID },
      request: { json: async () => ({ parent_id: null }) },
    });
    expect(res.status).toBe(200);
    expect(capturedBody).toEqual({ parent_id: null });
  });

  it("cuerpo sin ninguna clave reconocida → 400 validation_error, sin llamar al gateway", async () => {
    globalThis.fetch = noFetchExpected();
    const res = await patchOne({
      locals: SESSION_LOCALS,
      params: { id: DEP_ID },
      request: { json: async () => ({ created_at: "hoy", id: "otro" }) },
    });
    expect(res.status).toBe(400);
    expect((await res.json()).error).toBe("validation_error");
  });

  it("allowlist: descarta campos ajenos aunque los campos válidos estén presentes", async () => {
    let capturedBody;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ id: DEP_ID }), { status: 200 });
    });
    await patchOne({
      locals: SESSION_LOCALS,
      params: { id: DEP_ID },
      request: {
        json: async () => ({ nombre: "  Nueva Tesorería  ", id: "id-forzado", created_at: "hoy" }),
      },
    });
    expect(capturedBody).toEqual({ nombre: "Nueva Tesorería" });
  });
});
