// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/** Proxy del sobre firmado (FIRMA-2): sesión, allowlist de `acto`, ids escapados, relevo. */
import { describe, it, expect, afterEach, vi } from "vitest";
import { GET } from "./+server.js";

const LOCALS = { accessToken: "tok", user: { tenant_slug: "icetex", sub: "kc-1" } };
const ev = (acto, extra = {}) => ({
  locals: LOCALS,
  params: { id: "a/b", excepcionId: "33333333-3333-4333-8333-333333333333" },
  url: new URL(`http://x/s${acto === undefined ? "" : `?acto=${acto}`}`),
  ...extra,
});
afterEach(() => vi.restoreAllMocks());

const MAL = { id: "t", excepcionId: "no-es-uuid" };

describe("GET .../sobre", () => {
  it("401 sin sesión sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió llamar al gateway");
    });
    expect((await GET(ev("constancia", { locals: {} }))).status).toBe(401);
  });

  it("acto fuera del vocabulario es 422 sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió llamar al gateway");
    });
    expect((await GET(ev("../x"))).status).toBe(422);
  });

  it("escapa ids, pasa solo `acto` y relaya cuerpo y estado", async () => {
    const f = vi.fn(
      async () => new Response(JSON.stringify({ xml_firmado: "<a/>" }), { status: 200 }),
    );
    globalThis.fetch = f;
    const res = await GET(ev("revocacion"));
    expect((await res.json()).xml_firmado).toBe("<a/>");
    const [url, init] = f.mock.calls[0];
    expect(String(url)).toMatch(
      /\/transferencias\/a%2Fb\/acta\/firma-inobtenible\/33333333-3333-4333-8333-333333333333\/sobre\?acto=revocacion$/,
    );
    expect(init.headers.Authorization).toBe("Bearer tok");
  });

  it("relaya 404 y devuelve 503 si el gateway cae", async () => {
    globalThis.fetch = vi.fn(async () => new Response("{}", { status: 404 }));
    expect((await GET(ev("constancia"))).status).toBe(404);
    globalThis.fetch = vi.fn(() => {
      throw new Error("down");
    });
    expect((await GET(ev("constancia"))).status).toBe(503);
  });

  it("422 sin llamar al gateway si el id de la excepción no es un UUID", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió llamar al gateway");
    });
    const ev = { locals: LOCALS, params: MAL, url: new URL("http://x/s?acto=constancia") };
    expect((await GET(ev)).status).toBe(422);
  });
});
