// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * cuadro-server.test.js — Proxy /admin/trd/api/cuadro (C-4): 401, lista cerrada
 * de parámetros, propagación del 403 de incluir_borradores y del aviso de
 * organigrama con ciclo.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { GET } from "./+server.js";

const LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};

afterEach(() => vi.restoreAllMocks());

describe("GET /admin/trd/api/cuadro", () => {
  it("401 sin sesión, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await GET({ locals: {}, url: new URL("http://x/") });
    expect(res.status).toBe(401);
  });

  it("reenvía booleanos exactos y segun válido, con token y tenant (control positivo)", async () => {
    let capturedUrl;
    let capturedHeaders;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedHeaders = init.headers;
      return new Response(JSON.stringify({ secciones: [] }), { status: 200 });
    });
    const res = await GET({
      locals: LOCALS,
      url: new URL("http://x/?incluir_borradores=true&incluir_vacias=false&segun=vivo"),
    });
    expect(res.status).toBe(200);
    expect(capturedUrl).toContain("/trd/cuadro?");
    expect(capturedUrl).toContain("incluir_borradores=true");
    expect(capturedUrl).toContain("incluir_vacias=false");
    expect(capturedUrl).toContain("segun=vivo");
    expect(capturedHeaders.Authorization).toBe("Bearer tok-abc");
    expect(capturedHeaders["X-Tenant-Slug"]).toBe("icetex");
  });

  it("descarta valores fuera de la lista cerrada (no se inyecta nada al gateway)", async () => {
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input) => {
      capturedUrl = String(input);
      return new Response("{}", { status: 200 });
    });
    await GET({
      locals: LOCALS,
      url: new URL("http://x/?incluir_borradores=1&segun=otro&x=1&incluir_vacias=yes"),
    });
    expect(capturedUrl).not.toContain("incluir_borradores");
    expect(capturedUrl).not.toContain("incluir_vacias");
    expect(capturedUrl).not.toContain("segun");
    expect(capturedUrl).not.toContain("x=1");
  });

  it("propaga el 403 (borradores sin USUA_PERM_TRD) y el 409 organigrama_con_ciclo", async () => {
    for (const [status, error] of [
      [403, "forbidden"],
      [409, "organigrama_con_ciclo"],
    ]) {
      globalThis.fetch = vi.fn(
        async () => new Response(JSON.stringify({ error, status }), { status }),
      );
      const res = await GET({ locals: LOCALS, url: new URL("http://x/?incluir_borradores=true") });
      expect(res.status).toBe(status);
      expect((await res.json()).error).toBe(error);
    }
  });

  it("503 si el gateway no responde", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("caído");
    });
    const res = await GET({ locals: LOCALS, url: new URL("http://x/") });
    expect(res.status).toBe(503);
  });
});
