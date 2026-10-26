// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * contexto-server.test.js — Proxies BFF de /perfil/api/contexto (D-05,
 * E08/ADR-013). Cubre: guard 401, allowlist de depe_id y su validación de
 * forma (mismo rango 1..999999 que `URDCreate.depe_id`/`ContextSwitchRequest`).
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { GET as contextGet } from "./+server.js";
import { POST as switchPost } from "./switch/+server.js";

const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("GET /perfil/api/contexto", () => {
  it("401 sin locals.accessToken, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await contextGet({ locals: {} });
    expect(res.status).toBe(401);
  });

  it("reenvía identidad al gateway", async () => {
    let capturedUrl;
    let capturedHeaders;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedHeaders = init.headers;
      return new Response(
        JSON.stringify({ user_id: "u1", active_depe_id: 101, available_depts: [] }),
        { status: 200 },
      );
    });
    const res = await contextGet({ locals: SESSION_LOCALS });
    expect(res.status).toBe(200);
    expect(capturedUrl).toContain("/auth/context");
    expect(capturedHeaders.Authorization).toBe("Bearer tok-abc");
    expect(capturedHeaders["X-Tenant-Slug"]).toBe("icetex");
  });
});

describe("POST /perfil/api/contexto/switch", () => {
  it("401 sin locals.accessToken, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await switchPost({ locals: {}, request: { json: async () => ({ depe_id: 101 }) } });
    expect(res.status).toBe(401);
  });

  it.each([[undefined], [null], [0], [-1], [1000000], ["abc"], [1.5]])(
    "depe_id=%j inválido: 422 validation_error, sin llamar al gateway",
    async (depe_id) => {
      globalThis.fetch = vi.fn(() => {
        throw new Error("fetch no debió invocarse con depe_id inválido");
      });
      const res = await switchPost({
        locals: SESSION_LOCALS,
        request: { json: async () => ({ depe_id }) },
      });
      expect(res.status).toBe(422);
      expect((await res.json()).error).toBe("validation_error");
    },
  );

  it("allowlist: solo depe_id (coaccionado a entero) viaja al gateway", async () => {
    let capturedBody;
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedBody = JSON.parse(init.body);
      return new Response(
        JSON.stringify({ user_id: "u1", active_depe_id: 202, available_depts: [] }),
        { status: 200 },
      );
    });
    const res = await switchPost({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({ depe_id: "202", user_id: "otro-usuario", tenant_slug: "otro" }),
      },
    });
    expect(res.status).toBe(200);
    expect(capturedUrl).toContain("/auth/context/switch");
    expect(capturedBody).toEqual({ depe_id: 202 });
  });

  it("fallo de red: 503 connection_error", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new Error("network down");
    });
    const res = await switchPost({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ depe_id: 101 }) },
    });
    expect(res.status).toBe(503);
    expect((await res.json()).error).toBe("connection_error");
  });
});
