// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * tipos-server.test.js — Proxies /admin/trd/api/tipos[, /[id]] (D-05, E03/RF-MET-07).
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { GET as listTipos, POST as createTipo } from "./+server.js";
import { PATCH as updateTipo, DELETE as deleteTipo } from "./[id]/+server.js";

const TIPO_ID = "44444444-4444-4444-4444-444444444444";
const SERIE_ID = "55555555-5555-5555-5555-555555555555";
const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("GET /admin/trd/api/tipos", () => {
  it("401 sin sesión", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await listTipos({ locals: {}, url: new URL("http://x/") });
    expect(res.status).toBe(401);
  });

  it("filtra trd_serie_id inválido (no lo reenvía) y propaga activo válido", async () => {
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input) => {
      capturedUrl = String(input);
      return new Response(JSON.stringify([]), { status: 200 });
    });
    const res = await listTipos({
      locals: SESSION_LOCALS,
      url: new URL("http://x/?trd_serie_id=no-es-uuid&activo=true"),
    });
    expect(res.status).toBe(200);
    expect(capturedUrl).not.toContain("trd_serie_id");
    expect(capturedUrl).toContain("activo=true");
  });
});

describe("POST /admin/trd/api/tipos", () => {
  it("sin nombre: 400, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await createTipo({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ code: "OF" }) },
    });
    expect(res.status).toBe(400);
  });

  it("allowlist: solo campos de TipoDocumentalCreate viajan (mass-assignment)", async () => {
    let capturedBody;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ id: TIPO_ID }), { status: 201 });
    });

    const res = await createTipo({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({
          code: "OF",
          nombre: "Oficio",
          trd_serie_id: SERIE_ID,
          obligatorio: true,
          metadata_template: "oficio-v1",
          id: "otro-id",
          created_at: "2020-01-01T00:00:00Z",
        }),
      },
    });

    expect(res.status).toBe(201);
    expect(capturedBody).toEqual({
      code: "OF",
      nombre: "Oficio",
      trd_serie_id: SERIE_ID,
      obligatorio: true,
      metadata_template: "oficio-v1",
      activo: true,
    });
  });
});

describe("PATCH /admin/trd/api/tipos/[id]", () => {
  it("id inválido: 400, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await updateTipo({
      locals: SESSION_LOCALS,
      params: { id: "no-es-uuid" },
      request: { json: async () => ({}) },
    });
    expect(res.status).toBe(400);
  });

  it("escapa el id en la URL del gateway", async () => {
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input) => {
      capturedUrl = String(input);
      return new Response(JSON.stringify({ id: TIPO_ID }), { status: 200 });
    });
    const res = await updateTipo({
      locals: SESSION_LOCALS,
      params: { id: TIPO_ID },
      request: { json: async () => ({ nombre: "Nuevo" }) },
    });
    expect(res.status).toBe(200);
    expect(capturedUrl).toContain(`/tipos-documentales/${TIPO_ID}`);
  });
});

describe("DELETE /admin/trd/api/tipos/[id]", () => {
  it("401 sin sesión", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await deleteTipo({ locals: {}, params: { id: TIPO_ID } });
    expect(res.status).toBe(401);
  });

  it("204 del gateway se propaga sin cuerpo", async () => {
    globalThis.fetch = vi.fn(async () => new Response(null, { status: 204 }));
    const res = await deleteTipo({ locals: SESSION_LOCALS, params: { id: TIPO_ID } });
    expect(res.status).toBe(204);
  });
});
