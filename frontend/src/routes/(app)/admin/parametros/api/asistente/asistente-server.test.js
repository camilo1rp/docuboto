// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/** Proxy /admin/parametros/api/asistente (C-1): sesión, allowlist y reenvío. */
import { describe, it, expect, afterEach, vi } from "vitest";
import { GET, PUT } from "./+server.js";

const LOCALS = { accessToken: "tok", user: { tenant_slug: "icetex", sub: "u-1" } };
afterEach(() => vi.restoreAllMocks());

describe("proxy asistente", () => {
  it("401 sin sesión en GET y PUT, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    expect((await GET({ locals: {} })).status).toBe(401);
    expect((await PUT({ locals: {}, request: { json: async () => ({}) } })).status).toBe(401);
  });

  it("PUT rechaza un `habilitado` no booleano", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    for (const v of ["true", 1, null, undefined]) {
      const res = await PUT({ locals: LOCALS, request: { json: async () => ({ habilitado: v }) } });
      expect(res.status).toBe(400);
    }
  });

  it("PUT reenvía solo `habilitado` con bearer y tenant (control positivo)", async () => {
    let init;
    let url;
    globalThis.fetch = vi.fn(async (u, i) => {
      url = String(u);
      init = i;
      return new Response(JSON.stringify({ habilitado: true, cambiado: true }), { status: 200 });
    });
    const res = await PUT({
      locals: LOCALS,
      request: {
        json: async () => ({
          habilitado: true,
          declaracion_transmision: true,
          motivo: "  piloto ",
          tenant_slug: "otro",
          extra: 1,
        }),
      },
    });
    expect(res.status).toBe(200);
    expect(url).toMatch(/\/config\/asistente$/);
    expect(JSON.parse(init.body)).toEqual({
      habilitado: true,
      declaracion_transmision: true,
      motivo: "piloto",
    });
    expect(init.headers.Authorization).toBe("Bearer tok");
    expect(init.headers["X-Tenant-Slug"]).toBe("icetex");
  });

  it("propaga el 403 del backend", async () => {
    globalThis.fetch = vi.fn(
      async () => new Response(JSON.stringify({ detail: { error: "forbidden" } }), { status: 403 }),
    );
    const res = await PUT({
      locals: LOCALS,
      request: { json: async () => ({ habilitado: false }) },
    });
    expect(res.status).toBe(403);
  });
});
