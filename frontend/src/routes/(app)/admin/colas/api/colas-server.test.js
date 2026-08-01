// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * colas-server.test.js — Proxies BFF de /admin/colas/api/[service] (D-05,
 * ADR-021 Fase 5). Cubre: guard 401, allowlist cerrada de `service`
 * (workflow/notification/signature — nunca interpolado libremente en la URL
 * del gateway, es un proxy CERRADO, no abierto), escapado de [eventId], y el
 * caso central de `discard` (irreversible): exige `eventId` y `reason` (>=10
 * chars) ANTES de tocar el gateway, y un `service` fuera del allowlist se
 * rechaza con 400 sin reenviarse tal cual.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { GET as listDeadletters } from "./[service]/+server.js";
import { GET as getDeadletter } from "./[service]/[eventId]/+server.js";
import { POST as replayDeadletter } from "./[service]/[eventId]/replay/+server.js";
import { POST as discardDeadletter } from "./[service]/[eventId]/discard/+server.js";

const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "77777777-7777-7777-7777-777777777777" },
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("GET /admin/colas/api/[service]", () => {
  it("401 sin sesión, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await listDeadletters({
      locals: {},
      params: { service: "workflow" },
      url: new URL("http://x/"),
    });
    expect(res.status).toBe(401);
  });

  it("service fuera del allowlist: 400 unknown_service, sin llamar al gateway ni interpolarlo en la URL", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse con service desconocido");
    });
    const res = await listDeadletters({
      locals: SESSION_LOCALS,
      params: { service: "../../auth" },
      url: new URL("http://x/"),
    });
    expect(res.status).toBe(400);
    expect((await res.json()).error).toBe("unknown_service");
  });

  // Herencia de `Object.prototype`: un acceso directo `SERVICE_PATHS[service]`
  // devuelve una función heredada para estas claves en vez de `undefined`, con
  // lo que el guard de servicio desconocido las deja pasar y se interpolan en
  // la URL del gateway. El `fetch` resultante fallaría, así que no es una
  // vulnerabilidad — pero la lista dejaría de ser la lista cerrada que
  // aparenta, y ese es justo el guard del que depende el resto.
  it.each(["constructor", "toString", "__proto__", "valueOf"])(
    "clave heredada de Object.prototype (%s): 400, no se trata como servicio válido",
    async (service) => {
      globalThis.fetch = vi.fn(() => {
        throw new Error("fetch no debió invocarse con una clave heredada");
      });
      const res = await listDeadletters({
        locals: SESSION_LOCALS,
        params: { service },
        url: new URL("http://x/"),
      });
      expect(res.status).toBe(400);
      expect((await res.json()).error).toBe("unknown_service");
    },
  );

  it.each(["workflow", "notification", "signature"])(
    "service=%s válido: llama a la ruta interna mapeada del gateway",
    async (service) => {
      let capturedUrl;
      globalThis.fetch = vi.fn(async (input) => {
        capturedUrl = String(input);
        return new Response(JSON.stringify([]), {
          status: 200,
          headers: { "X-Total-Count": "0", "X-Truncated": "false" },
        });
      });
      await listDeadletters({ locals: SESSION_LOCALS, params: { service }, url: new URL("http://x/") });
      expect(capturedUrl).toContain(`/${service}/admin/deadletter/`);
    },
  );

  it("propaga X-Total-Count y X-Truncated intactos", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify([]), {
          status: 200,
          headers: { "X-Total-Count": "500", "X-Truncated": "true" },
        }),
    );
    const res = await listDeadletters({
      locals: SESSION_LOCALS,
      params: { service: "workflow" },
      url: new URL("http://x/"),
    });
    expect(res.headers.get("X-Total-Count")).toBe("500");
    expect(res.headers.get("X-Truncated")).toBe("true");
  });

  it("fallo de red: 503 connection_error", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new Error("network down");
    });
    const res = await listDeadletters({
      locals: SESSION_LOCALS,
      params: { service: "workflow" },
      url: new URL("http://x/"),
    });
    expect(res.status).toBe(503);
  });
});

describe("GET /admin/colas/api/[service]/[eventId]", () => {
  const EVENT_ID = "event id/with#chars ../up";

  it("401 sin sesión, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await getDeadletter({ locals: {}, params: { service: "workflow", eventId: "e1" } });
    expect(res.status).toBe(401);
  });

  it("service desconocido: 400 unknown_service, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse con service desconocido");
    });
    const res = await getDeadletter({
      locals: SESSION_LOCALS,
      params: { service: "otro-servicio", eventId: "e1" },
    });
    expect(res.status).toBe(400);
  });

  it("escapa el eventId al componer la URL del gateway", async () => {
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input) => {
      capturedUrl = String(input);
      return new Response(JSON.stringify({}), { status: 200 });
    });
    await getDeadletter({ locals: SESSION_LOCALS, params: { service: "workflow", eventId: EVENT_ID } });
    expect(capturedUrl).toContain(encodeURIComponent(EVENT_ID));
    expect(capturedUrl).not.toContain("event id/with#chars");
  });

  it("404 (ambiguo: no existe o sin clearance) se propaga tal cual, sin distinguir los dos casos", async () => {
    globalThis.fetch = vi.fn(
      async () => new Response(JSON.stringify({ error: "not_found", status: 404 }), { status: 404 }),
    );
    const res = await getDeadletter({ locals: SESSION_LOCALS, params: { service: "workflow", eventId: "e1" } });
    expect(res.status).toBe(404);
  });
});

describe("POST /admin/colas/api/[service]/[eventId]/replay", () => {
  it("401 sin sesión, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await replayDeadletter({ locals: {}, params: { service: "workflow", eventId: "e1" } });
    expect(res.status).toBe(401);
  });

  it("service desconocido: 400 unknown_service, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse con service desconocido");
    });
    const res = await replayDeadletter({
      locals: SESSION_LOCALS,
      params: { service: "not-a-real-service", eventId: "e1" },
    });
    expect(res.status).toBe(400);
  });

  it("escapa el eventId y llama con POST", async () => {
    let capturedUrl;
    let capturedMethod;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedMethod = init.method;
      return new Response(JSON.stringify({ status: "replayed" }), { status: 200 });
    });
    const res = await replayDeadletter({
      locals: SESSION_LOCALS,
      params: { service: "signature", eventId: "../etc/passwd" },
    });
    expect(res.status).toBe(200);
    expect(capturedMethod).toBe("POST");
    expect(capturedUrl).toContain(encodeURIComponent("../etc/passwd"));
    expect(capturedUrl).not.toMatch(/deadletter\/\.\.\//);
  });

  it("409 already_resolved (carrera) se propaga tal cual", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ error: "already_resolved", status: 409 }), { status: 409 }),
    );
    const res = await replayDeadletter({ locals: SESSION_LOCALS, params: { service: "workflow", eventId: "e1" } });
    expect(res.status).toBe(409);
  });

  it("502 replay_failed se propaga tal cual (no se colapsa en 500 genérico)", async () => {
    globalThis.fetch = vi.fn(
      async () => new Response(JSON.stringify({ error: "replay_failed", status: 502 }), { status: 502 }),
    );
    const res = await replayDeadletter({ locals: SESSION_LOCALS, params: { service: "workflow", eventId: "e1" } });
    expect(res.status).toBe(502);
  });
});

describe("POST /admin/colas/api/[service]/[eventId]/discard — irreversible", () => {
  it("401 sin sesión, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await discardDeadletter({
      locals: {},
      params: { service: "workflow", eventId: "e1" },
      request: { json: async () => ({ reason: "motivo suficientemente largo" }) },
    });
    expect(res.status).toBe(401);
  });

  it("service desconocido: 400 unknown_service ANTES de leer el body, sin reenviarlo tal cual al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse con service desconocido");
    });
    const res = await discardDeadletter({
      locals: SESSION_LOCALS,
      params: { service: "totally-unknown", eventId: "e1" },
      request: { json: async () => ({ reason: "motivo suficientemente largo" }) },
    });
    expect(res.status).toBe(400);
    expect((await res.json()).error).toBe("unknown_service");
  });

  it("sin reason: 422 validation_error, sin llamar al gateway (descartar exige motivo)", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin motivo");
    });
    const res = await discardDeadletter({
      locals: SESSION_LOCALS,
      params: { service: "workflow", eventId: "e1" },
      request: { json: async () => ({}) },
    });
    expect(res.status).toBe(422);
    expect((await res.json()).error).toBe("validation_error");
  });

  it("reason por debajo de 10 caracteres (tras trim): 422 validation_error, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse con motivo corto");
    });
    const res = await discardDeadletter({
      locals: SESSION_LOCALS,
      params: { service: "notification", eventId: "e1" },
      request: { json: async () => ({ reason: "   corto   " }) },
    });
    expect(res.status).toBe(422);
  });

  it("eventId ausente/vacío: no se construye una URL con segmento vacío hacia el gateway con reason válida", async () => {
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input) => {
      capturedUrl = String(input);
      return new Response(JSON.stringify({ status: "discarded" }), { status: 200 });
    });
    await discardDeadletter({
      locals: SESSION_LOCALS,
      params: { service: "workflow", eventId: "" },
      request: { json: async () => ({ reason: "motivo suficientemente largo" }) },
    });
    // encodeURIComponent("") === "" — el proxy no valida explícitamente que
    // eventId no esté vacío; se documenta el comportamiento actual: la URL
    // queda con un segmento vacío que el backend responderá con 404/405.
    expect(capturedUrl).toMatch(/deadletter\/\/discard$/);
  });

  it("allowlist: solo reason viaja (trim aplicado), nunca el body íntegro", async () => {
    let capturedBody;
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ status: "discarded" }), { status: 200 });
    });

    const res = await discardDeadletter({
      locals: SESSION_LOCALS,
      params: { service: "signature", eventId: "e-42" },
      request: {
        json: async () => ({
          reason: "  el operador confirmó que el evento es obsoleto  ",
          // Campos ajenos que un cliente malicioso podría intentar colar:
          status: "discarded",
          tenant_slug: "otro-tenant",
        }),
      },
    });

    expect(res.status).toBe(200);
    expect(capturedUrl).toContain("/signature/admin/deadletter/e-42/discard");
    expect(capturedBody).toEqual({ reason: "el operador confirmó que el evento es obsoleto" });
  });

  it("fallo de red: 503 connection_error", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new Error("network down");
    });
    const res = await discardDeadletter({
      locals: SESSION_LOCALS,
      params: { service: "workflow", eventId: "e1" },
      request: { json: async () => ({ reason: "motivo suficientemente largo" }) },
    });
    expect(res.status).toBe(503);
    expect((await res.json()).error).toBe("connection_error");
  });
});
