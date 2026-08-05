// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * notificaciones-server.test.js — Proxies BFF de /admin/notificaciones/api
 * (D-05). Cubre: guard 401, clamp de `limit` en el historial, allowlist de
 * POST /webhooks (url/event_types/secret — nunca el body íntegro), escapado
 * del [id] en DELETE /webhooks/[id], y el hallazgo de esta auditoría: el
 * proxy de creación de webhooks NO valida el formato/esquema de la URL de
 * destino más allá de "no vacía" (ver el `it` marcado HALLAZGO abajo).
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { GET as getHistorial } from "./historial/+server.js";
import { GET as listWebhooks, POST as createWebhook } from "./webhooks/+server.js";
import { DELETE as deleteWebhook } from "./webhooks/[id]/+server.js";

const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "77777777-7777-7777-7777-777777777777" },
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("GET /admin/notificaciones/api/historial", () => {
  it("401 sin sesión, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await getHistorial({ locals: {}, url: new URL("http://x/") });
    expect(res.status).toBe(401);
  });

  it("clampa limit a 100 como máximo (mismo límite que el backend)", async () => {
    globalThis.fetch = vi.fn(async (url) => {
      expect(url).toContain("limit=100");
      return new Response(JSON.stringify([]), { status: 200 });
    });
    await getHistorial({ locals: SESSION_LOCALS, url: new URL("http://x/?limit=9999") });
  });

  it("clampa limit a 1 como mínimo", async () => {
    globalThis.fetch = vi.fn(async (url) => {
      expect(url).toContain("limit=1");
      return new Response(JSON.stringify([]), { status: 200 });
    });
    await getHistorial({ locals: SESSION_LOCALS, url: new URL("http://x/?limit=-5") });
  });

  it("X-User-Id/X-Tenant-Slug viajan desde locals", async () => {
    let capturedHeaders;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedHeaders = init?.headers ?? {};
      return new Response(JSON.stringify([]), { status: 200 });
    });
    await getHistorial({ locals: SESSION_LOCALS, url: new URL("http://x/") });
    expect(capturedHeaders["X-Tenant-Slug"]).toBe("icetex");
    expect(capturedHeaders["X-User-Id"]).toBe(SESSION_LOCALS.user.sub);
  });

  it("fallo de red: 503 connection_error", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new Error("network down");
    });
    const res = await getHistorial({ locals: SESSION_LOCALS, url: new URL("http://x/") });
    expect(res.status).toBe(503);
    expect((await res.json()).error).toBe("connection_error");
  });
});

describe("GET /admin/notificaciones/api/webhooks", () => {
  it("401 sin sesión, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await listWebhooks({ locals: {} });
    expect(res.status).toBe(401);
  });

  it("propaga X-Total-Count del gateway", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify([]), { status: 200, headers: { "X-Total-Count": "3" } }),
    );
    const res = await listWebhooks({ locals: SESSION_LOCALS });
    expect(res.headers.get("X-Total-Count")).toBe("3");
  });
});

describe("POST /admin/notificaciones/api/webhooks", () => {
  it("401 sin sesión, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await createWebhook({
      locals: {},
      request: { json: async () => ({ url: "https://example.com/hook" }) },
    });
    expect(res.status).toBe(401);
  });

  it("sin url: 422 validation_error, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin url");
    });
    const res = await createWebhook({
      locals: SESSION_LOCALS,
      request: { json: async () => ({}) },
    });
    expect(res.status).toBe(422);
    expect((await res.json()).error).toBe("validation_error");
  });

  it("allowlist: solo url/event_types/secret viajan, nunca el body íntegro", async () => {
    let capturedBody;
    let capturedHeaders;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedBody = JSON.parse(init.body);
      capturedHeaders = init.headers;
      return new Response(JSON.stringify({ id: "wh-1" }), { status: 201 });
    });

    const res = await createWebhook({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({
          url: "https://example.com/hook",
          event_types: [" document.created ", "workflow.assigned"],
          secret: "shh",
          // Campos ajenos que un cliente malicioso podría intentar colar:
          id: "otro-id",
          tenant_slug: "otro-tenant",
          is_active: true,
        }),
      },
    });

    expect(res.status).toBe(201);
    expect(capturedBody).toEqual({
      url: "https://example.com/hook",
      event_types: ["document.created", "workflow.assigned"],
      secret: "shh",
    });
    expect(capturedHeaders.Authorization).toBe("Bearer tok-abc");
    expect(capturedHeaders["X-Tenant-Slug"]).toBe("icetex");
  });

  it("event_types no-array se normaliza a []", async () => {
    let capturedBody;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ id: "wh-1" }), { status: 201 });
    });
    await createWebhook({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({ url: "https://example.com/hook", event_types: "not-an-array" }),
      },
    });
    expect(capturedBody.event_types).toEqual([]);
  });

  it("HALLAZGO: el proxy NO valida el esquema/formato de la url de destino — cualquier string no vacío pasa tal cual al gateway (SSRF potencial delegado al backend, no mitigado aquí)", async () => {
    let capturedBody;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ id: "wh-1" }), { status: 201 });
    });
    // No es una URL http(s): ni siquiera tiene forma de URL. El proxy actual
    // solo comprueba "string no vacío tras trim" — no rechaza esto.
    const res = await createWebhook({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ url: "not-a-valid-url-at-all" }) },
    });
    expect(res.status).toBe(201);
    expect(capturedBody.url).toBe("not-a-valid-url-at-all");
  });

  it("fallo de red: 503 connection_error", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new Error("network down");
    });
    const res = await createWebhook({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ url: "https://example.com/hook" }) },
    });
    expect(res.status).toBe(503);
    expect((await res.json()).error).toBe("connection_error");
  });
});

describe("DELETE /admin/notificaciones/api/webhooks/[id]", () => {
  const WEBHOOK_ID = "id with space #frag ../traversal";

  it("401 sin sesión, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await deleteWebhook({ locals: {}, params: { id: "wh-1" } });
    expect(res.status).toBe(401);
  });

  it("escapa el id (con espacio/#/../) al componer la URL del gateway", async () => {
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input) => {
      capturedUrl = String(input);
      return new Response(null, { status: 204 });
    });
    const res = await deleteWebhook({ locals: SESSION_LOCALS, params: { id: WEBHOOK_ID } });
    expect(res.status).toBe(204);
    expect(capturedUrl).toContain(encodeURIComponent(WEBHOOK_ID));
    expect(capturedUrl).not.toContain(" ");
    expect(capturedUrl).not.toMatch(/webhooks\/id with/);
  });

  it("404 del gateway se propaga tal cual", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ error: "not_found", status: 404 }), { status: 404 }),
    );
    const res = await deleteWebhook({ locals: SESSION_LOCALS, params: { id: "wh-1" } });
    expect(res.status).toBe(404);
  });

  it("fallo de red: 503 connection_error", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new Error("network down");
    });
    const res = await deleteWebhook({ locals: SESSION_LOCALS, params: { id: "wh-1" } });
    expect(res.status).toBe(503);
    expect((await res.json()).error).toBe("connection_error");
  });
});
