// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * auditoria-server.test.js — Proxies BFF de /admin/auditoria/api (D-05, E08).
 *
 * Cubre: guard 401, allowlist de filtros (solo action/object_type/object_ref/
 * actor viajan — un filtro ajeno del cliente no llega al gateway), reenvío
 * intacto de X-Total-Count, y propagación de errores del gateway en la
 * re-verificación de la cadena de hash.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { GET as listAudit } from "./+server.js";
import { GET as verifyChain } from "./verify/+server.js";

const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "77777777-7777-7777-7777-777777777777" },
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("GET /admin/auditoria/api", () => {
  it("401 sin sesión, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await listAudit({ locals: {}, url: new URL("http://x/") });
    expect(res.status).toBe(401);
  });

  it("allowlist: solo action/object_type/object_ref/actor viajan, un filtro ajeno del cliente se descarta", async () => {
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input) => {
      capturedUrl = String(input);
      return new Response(JSON.stringify([]), { status: 200, headers: { "X-Total-Count": "0" } });
    });
    await listAudit({
      locals: SESSION_LOCALS,
      url: new URL(
        "http://x/?action=create&object_type=document&object_ref=r1&actor=u1&clearance=99&tenant_slug=otro",
      ),
    });
    expect(capturedUrl).toContain("action=create");
    expect(capturedUrl).toContain("object_type=document");
    expect(capturedUrl).toContain("object_ref=r1");
    expect(capturedUrl).toContain("actor=u1");
    expect(capturedUrl).not.toContain("clearance");
    expect(capturedUrl).not.toContain("tenant_slug");
  });

  it("X-User-Id/X-Tenant-Slug viajan desde locals, no desde el querystring", async () => {
    let capturedHeaders;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedHeaders = init.headers;
      return new Response(JSON.stringify([]), { status: 200, headers: { "X-Total-Count": "0" } });
    });
    await listAudit({
      locals: SESSION_LOCALS,
      url: new URL("http://x/?actor=usuario-suplantado"),
    });
    expect(capturedHeaders["X-Tenant-Slug"]).toBe("icetex");
    expect(capturedHeaders["X-User-Id"]).toBe(SESSION_LOCALS.user.sub);
  });

  it("propaga X-Total-Count del gateway intacto", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify([]), { status: 200, headers: { "X-Total-Count": "37" } }),
    );
    const res = await listAudit({ locals: SESSION_LOCALS, url: new URL("http://x/") });
    expect(res.headers.get("X-Total-Count")).toBe("37");
  });

  it("propaga un 403 del gateway tal cual (no lo colapsa en 500)", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ error: "forbidden", status: 403 }), { status: 403 }),
    );
    const res = await listAudit({ locals: SESSION_LOCALS, url: new URL("http://x/") });
    expect(res.status).toBe(403);
  });

  it("fallo de red: 503 connection_error", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new Error("network down");
    });
    const res = await listAudit({ locals: SESSION_LOCALS, url: new URL("http://x/") });
    expect(res.status).toBe(503);
    expect((await res.json()).error).toBe("connection_error");
  });
});

describe("GET /admin/auditoria/api/verify", () => {
  it("401 sin sesión, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await verifyChain({ locals: {} });
    expect(res.status).toBe(401);
  });

  it("cadena íntegra: propaga el 200 y el cuerpo del gateway tal cual", async () => {
    globalThis.fetch = vi.fn(
      async () => new Response(JSON.stringify({ valid: true, checked: 120 }), { status: 200 }),
    );
    const res = await verifyChain({ locals: SESSION_LOCALS });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ valid: true, checked: 120 });
  });

  it("cadena comprometida: propaga el cuerpo con valid=false tal cual", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ valid: false, broken_at: "entry-42" }), { status: 200 }),
    );
    const res = await verifyChain({ locals: SESSION_LOCALS });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ valid: false, broken_at: "entry-42" });
  });

  it("un 5xx del gateway (verificación no ejecutable) se propaga tal cual, no se colapsa en 200", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ error: "internal_error", status: 500 }), { status: 500 }),
    );
    const res = await verifyChain({ locals: SESSION_LOCALS });
    expect(res.status).toBe(500);
  });

  it("fallo de red: 503 connection_error (no null silencioso)", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new Error("network down");
    });
    const res = await verifyChain({ locals: SESSION_LOCALS });
    expect(res.status).toBe(503);
    expect((await res.json()).error).toBe("connection_error");
  });
});
