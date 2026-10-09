// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/** Proxy del reintento WORM (revocacion/worm/reintentar): sesión, ids escapados, relevo. */
import { describe, it, expect, afterEach, vi } from "vitest";
import { POST } from "./+server.js";

const LOCALS = { accessToken: "tok", user: { tenant_slug: "icetex", sub: "kc-1" } };
afterEach(() => vi.restoreAllMocks());

const MAL = { id: "t", excepcionId: "no-es-uuid" };

describe("POST .../revocacion/worm/reintentar", () => {
  it("401 sin sesión y sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió llamar al gateway");
    });
    const res = await POST({
      locals: {},
      params: { id: "t", excepcionId: "33333333-3333-4333-8333-333333333333" },
    });
    expect(res.status).toBe(401);
  });

  it("escapa los ids, usa POST, propaga token y tenant y relaya el cuerpo", async () => {
    const f = vi.fn(
      async () => new Response(JSON.stringify({ repuestos: 1, artefactos: {} }), { status: 200 }),
    );
    globalThis.fetch = f;
    const res = await POST({
      locals: LOCALS,
      params: { id: "a/b", excepcionId: "33333333-3333-4333-8333-333333333333" },
    });
    expect(res.status).toBe(200);
    expect((await res.json()).repuestos).toBe(1);
    const [url, init] = f.mock.calls[0];
    expect(String(url)).toMatch(
      /\/transferencias\/a%2Fb\/acta\/firma-inobtenible\/33333333-3333-4333-8333-333333333333\/revocacion\/worm\/reintentar$/,
    );
    expect(init.method).toBe("POST");
    expect(init.headers.Authorization).toBe("Bearer tok");
    expect(init.headers["X-Tenant-Slug"]).toBe("icetex");
  });

  it("relaya un 403 y devuelve 503 si el gateway no responde", async () => {
    globalThis.fetch = vi.fn(async () => new Response("{}", { status: 403 }));
    expect(
      (
        await POST({
          locals: LOCALS,
          params: { id: "t", excepcionId: "33333333-3333-4333-8333-333333333333" },
        })
      ).status,
    ).toBe(403);
    globalThis.fetch = vi.fn(() => {
      throw new Error("down");
    });
    expect(
      (
        await POST({
          locals: LOCALS,
          params: { id: "t", excepcionId: "33333333-3333-4333-8333-333333333333" },
        })
      ).status,
    ).toBe(503);
  });

  it("422 sin llamar al gateway si el id de la excepción no es un UUID", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió llamar al gateway");
    });
    const ev = { locals: LOCALS, params: MAL, url: new URL("http://x/s?acto=constancia") };
    expect((await POST(ev)).status).toBe(422);
  });
});
