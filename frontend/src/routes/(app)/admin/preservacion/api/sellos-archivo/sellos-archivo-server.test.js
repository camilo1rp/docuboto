// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
/**
 * sellos-archivo-server.test.js — Proxies BFF de la faceta de sellos (C-6):
 * 401, lista cerrada de situaciones, tope de size, X-Total-Count reenviado y 403
 * propagado.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { GET as getLista } from "./+server.js";
import { GET as getResumen } from "./resumen/+server.js";

const LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "77777777-7777-7777-7777-777777777777" },
};
afterEach(() => vi.restoreAllMocks());

describe("GET /admin/preservacion/api/sellos-archivo", () => {
  it("401 sin sesión, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    expect((await getLista({ locals: {}, url: new URL("http://x/") })).status).toBe(401);
    expect((await getResumen({ locals: {} })).status).toBe(401);
  });

  it("reenvía X-Total-Count, situaciones repetidas válidas, dias, page y size (control positivo)", async () => {
    let capturedUrl;
    let capturedHeaders;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedHeaders = init.headers;
      return new Response("[]", { status: 200, headers: { "X-Total-Count": "57" } });
    });
    const res = await getLista({
      locals: LOCALS,
      url: new URL("http://x/?situacion=vencido&situacion=por_vencer&dias=30&page=2&size=50"),
    });
    expect(res.status).toBe(200);
    expect(res.headers.get("X-Total-Count")).toBe("57");
    expect(capturedUrl).toContain("/signature/admin/sellos-archivo?");
    expect(capturedUrl.match(/situacion=/g)).toHaveLength(2);
    expect(capturedUrl).toContain("dias=30");
    expect(capturedUrl).toContain("page=2");
    expect(capturedUrl).toContain("size=50");
    expect(capturedHeaders.Authorization).toBe("Bearer tok-abc");
    expect(capturedHeaders["X-Tenant-Slug"]).toBe("icetex");
  });

  it("descarta situaciones y dias fuera de contrato y acota size a 100", async () => {
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input) => {
      capturedUrl = String(input);
      return new Response("[]", { status: 200 });
    });
    await getLista({
      locals: LOCALS,
      url: new URL("http://x/?situacion=%27%3B--&situacion=otro&dias=abc&size=9999&page=-4"),
    });
    expect(capturedUrl).not.toContain("situacion");
    expect(capturedUrl).not.toContain("dias");
    expect(capturedUrl).toContain("size=100");
    expect(capturedUrl).toContain("page=1");
  });

  it("propaga el 403 (sin USUA_PERM_ADMIN ≥3) y no inventa X-Total-Count", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ error: "forbidden", status: 403 }), { status: 403 }),
    );
    const res = await getLista({ locals: LOCALS, url: new URL("http://x/") });
    expect(res.status).toBe(403);
    expect(res.headers.get("X-Total-Count")).toBeNull();
  });

  it("resumen: propaga cuerpo y status; 503 si el gateway cae", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ por_vencer: 2, vencido: 1, margen_dias: 30 }), {
          status: 200,
        }),
    );
    const ok = await getResumen({ locals: LOCALS });
    expect((await ok.json()).por_vencer).toBe(2);
    globalThis.fetch = vi.fn(() => {
      throw new Error("caído");
    });
    expect((await getResumen({ locals: LOCALS })).status).toBe(503);
  });
});
