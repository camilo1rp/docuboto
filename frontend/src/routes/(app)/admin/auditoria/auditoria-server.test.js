// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * auditoria-server.test.js — `load` de /admin/auditoria (+page.server.js,
 * E08, ADR-008). Cubre los cuatro estados de `verify` que la vista debe
 * distinguir: íntegra, comprometida, no ejecutable (5xx/red caída), y el caso
 * añadido recientemente — un 200 con cuerpo JSON ilegible DEBE producir
 * `verifyError`, nunca `verifyResult = null` silencioso (ese silencio se
 * resolvería en la vista como "cadena comprometida", una afirmación falsa de
 * ruptura que nadie comprobó).
 */
import { describe, it, expect, vi } from "vitest";
import { load } from "./+page.server.js";

const parentWith =
  (tenant_slug = "icetex", sub = "admin-1") =>
  async () => ({
    user: { tenant_slug, sub },
  });

function fakeFetch({ auditOk = true, verifyImpl } = {}) {
  return vi.fn(async (input) => {
    const url = String(input);
    if (url.includes("/audit/verify")) {
      return verifyImpl();
    }
    if (url.includes("/audit")) {
      if (!auditOk)
        return { ok: false, status: 500, json: async () => ({ detail: "falla auditoria" }) };
      return {
        ok: true,
        status: 200,
        headers: { get: (h) => (h === "X-Total-Count" ? "1" : null) },
        json: async () => [{ id: "a1" }],
      };
    }
    throw new Error(`fetch no esperado: ${url}`);
  });
}

describe("load /admin/auditoria — estados de verificación de la cadena de hash", () => {
  it("cadena íntegra: verifyResult con valid=true, sin verifyError", async () => {
    const fetch = fakeFetch({
      verifyImpl: async () => ({
        ok: true,
        status: 200,
        json: async () => ({ valid: true, checked: 42 }),
      }),
    });
    const result = await load({ cookies: { get: () => "tok" }, parent: parentWith(), fetch });
    expect(result.verifyResult).toEqual({ valid: true, checked: 42 });
    expect(result.verifyError).toBeNull();
  });

  it("cadena comprometida: verifyResult con valid=false, sin verifyError", async () => {
    const fetch = fakeFetch({
      verifyImpl: async () => ({
        ok: true,
        status: 200,
        json: async () => ({ valid: false, broken_at: "entry-9" }),
      }),
    });
    const result = await load({ cookies: { get: () => "tok" }, parent: parentWith(), fetch });
    expect(result.verifyResult).toEqual({ valid: false, broken_at: "entry-9" });
    expect(result.verifyError).toBeNull();
  });

  it("verificación no ejecutable — 5xx del gateway: verifyResult queda null y verifyError se llena", async () => {
    const fetch = fakeFetch({
      verifyImpl: async () => ({
        ok: false,
        status: 500,
        json: async () => ({ detail: "fallo interno al verificar" }),
      }),
    });
    const result = await load({ cookies: { get: () => "tok" }, parent: parentWith(), fetch });
    expect(result.verifyResult).toBeNull();
    expect(result.verifyError).toBe("fallo interno al verificar");
  });

  it("verificación no ejecutable — red caída: verifyResult queda null y verifyError se llena", async () => {
    const fetch = fakeFetch({
      verifyImpl: async () => {
        throw new Error("network down");
      },
    });
    const result = await load({ cookies: { get: () => "tok" }, parent: parentWith(), fetch });
    expect(result.verifyResult).toBeNull();
    expect(result.verifyError).toBe("No se pudo conectar al servidor para verificar la cadena.");
  });

  it("200 con cuerpo JSON ilegible produce verifyError explícito, NUNCA verifyResult=null silencioso sin error (guard reciente)", async () => {
    const fetch = fakeFetch({
      verifyImpl: async () => ({
        ok: true,
        status: 200,
        json: async () => {
          throw new SyntaxError("Unexpected token in JSON");
        },
      }),
    });
    const result = await load({ cookies: { get: () => "tok" }, parent: parentWith(), fetch });
    expect(result.verifyResult).toBeNull();
    // El punto central del guard: verifyError NO puede ser null cuando
    // verifyResult también es null tras un 200 — de lo contrario la vista
    // resolvería el silencio como "cadena comprometida" sin que nadie lo
    // haya comprobado realmente.
    expect(result.verifyError).toBeTruthy();
    expect(typeof result.verifyError).toBe("string");
  });
});

describe("load /admin/auditoria — listado principal", () => {
  it("propaga Authorization/X-Tenant-Slug/X-User-Id desde cookies/parent", async () => {
    const fetch = fakeFetch({
      verifyImpl: async () => ({ ok: true, status: 200, json: async () => ({ valid: true }) }),
    });
    await load({
      cookies: { get: () => "tok-xyz" },
      parent: parentWith("icetex", "admin-9"),
      fetch,
    });
    const [, opts] = fetch.mock.calls[0];
    expect(opts.headers.Authorization).toBe("Bearer tok-xyz");
    expect(opts.headers["X-Tenant-Slug"]).toBe("icetex");
    expect(opts.headers["X-User-Id"]).toBe("admin-9");
  });

  it("fallo en /audit no impide que la verificación de la cadena se ejecute igual", async () => {
    const fetch = fakeFetch({
      auditOk: false,
      verifyImpl: async () => ({ ok: true, status: 200, json: async () => ({ valid: true }) }),
    });
    const result = await load({ cookies: { get: () => "tok" }, parent: parentWith(), fetch });
    expect(result.loadError).toBeTruthy();
    expect(result.verifyResult).toEqual({ valid: true });
    expect(result.verifyError).toBeNull();
  });
});
