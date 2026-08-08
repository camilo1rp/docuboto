// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * totp-server.test.js — Proxies BFF de /perfil/api/totp (D-05, E06/RFC 6238).
 *
 * Cubre: guard 401 en cada handler, allowlist anti mass-assignment,
 * validación de forma del código de 6 dígitos y propagación de
 * X-Tenant-Slug/X-User-Id.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { GET as statusGet, DELETE as revokeDelete } from "./+server.js";
import { POST as enrollPost } from "./enroll/+server.js";
import { POST as activatePost } from "./activate/+server.js";
import { POST as recoveryPost } from "./recovery-codes/+server.js";

const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("GET /perfil/api/totp", () => {
  it("401 sin locals.accessToken, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await statusGet({ locals: {} });
    expect(res.status).toBe(401);
  });

  it("reenvía Authorization/X-Tenant-Slug/X-User-Id y marca Cache-Control: no-store", async () => {
    let capturedHeaders;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedHeaders = init.headers;
      return new Response(JSON.stringify({ estado: "activo" }), { status: 200 });
    });
    const res = await statusGet({ locals: SESSION_LOCALS });
    expect(res.status).toBe(200);
    expect(capturedHeaders.Authorization).toBe("Bearer tok-abc");
    expect(capturedHeaders["X-Tenant-Slug"]).toBe("icetex");
    expect(capturedHeaders["X-User-Id"]).toBe(SESSION_LOCALS.user.sub);
    expect(res.headers.get("Cache-Control")).toBe("no-store");
  });

  it("fallo de red: 503 connection_error", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new Error("network down");
    });
    const res = await statusGet({ locals: SESSION_LOCALS });
    expect(res.status).toBe(503);
    expect((await res.json()).error).toBe("connection_error");
  });
});

describe("POST /perfil/api/totp/enroll", () => {
  it("401 sin locals.accessToken, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await enrollPost({ locals: {}, request: { json: async () => ({}) } });
    expect(res.status).toBe(401);
  });

  it("allowlist: solo step_up_totp_code se reenvía; body vacío si no llega", async () => {
    let capturedBody;
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedBody = JSON.parse(init.body);
      return new Response(
        JSON.stringify({
          otpauth_uri: "otpauth://totp/x?secret=ABC",
          estado: "pendiente_activacion",
        }),
        { status: 201 },
      );
    });
    const res = await enrollPost({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ actor: "otro-usuario", tenant_slug: "otro-tenant" }) },
    });
    expect(res.status).toBe(201);
    expect(capturedUrl).toContain("/auth/me/totp/enroll");
    expect(capturedBody).toEqual({});
    expect(res.headers.get("Cache-Control")).toBe("no-store");
  });

  it("con step_up_totp_code presente, se reenvía trimmed", async () => {
    let capturedBody;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ otpauth_uri: "x", estado: "pendiente_activacion" }), {
        status: 201,
      });
    });
    await enrollPost({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ step_up_totp_code: " 123456 " }) },
    });
    expect(capturedBody).toEqual({ step_up_totp_code: "123456" });
  });
});

describe("POST /perfil/api/totp/activate", () => {
  it("401 sin locals.accessToken, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await activatePost({
      locals: {},
      request: { json: async () => ({ code: "123456" }) },
    });
    expect(res.status).toBe(401);
  });

  it.each([[undefined], [null], [""], ["12345"], ["1234567"], ["abcdef"], [123456]])(
    "code=%j inválido: 422 validation_error, sin llamar al gateway",
    async (code) => {
      globalThis.fetch = vi.fn(() => {
        throw new Error("fetch no debió invocarse con código inválido");
      });
      const res = await activatePost({
        locals: SESSION_LOCALS,
        request: { json: async () => ({ code }) },
      });
      expect(res.status).toBe(422);
      expect((await res.json()).error).toBe("validation_error");
    },
  );

  it("code de 6 dígitos válido: se reenvía tal cual al gateway", async () => {
    let capturedBody;
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedBody = JSON.parse(init.body);
      return new Response(
        JSON.stringify({ estado: "activo", activated_at: "2026-01-01T00:00:00Z" }),
        {
          status: 200,
        },
      );
    });
    const res = await activatePost({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ code: "654321" }) },
    });
    expect(res.status).toBe(200);
    expect(capturedUrl).toContain("/auth/me/totp/activate");
    expect(capturedBody).toEqual({ code: "654321" });
  });
});

describe("DELETE /perfil/api/totp", () => {
  it("401 sin locals.accessToken, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await revokeDelete({ locals: {}, request: { json: async () => ({}) } });
    expect(res.status).toBe(401);
  });

  it("allowlist: solo step_up_totp_code viaja; campos ajenos se descartan", async () => {
    let capturedBody;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ estado: "revocado" }), { status: 200 });
    });
    const res = await revokeDelete({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({ step_up_totp_code: "111222", user_id: "otro-usuario" }),
      },
    });
    expect(res.status).toBe(200);
    expect(capturedBody).toEqual({ step_up_totp_code: "111222" });
  });

  it("sin step_up_totp_code: body vacío (el backend decide si lo exige)", async () => {
    let capturedBody;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ estado: "revocado" }), { status: 200 });
    });
    await revokeDelete({ locals: SESSION_LOCALS, request: { json: async () => ({}) } });
    expect(capturedBody).toEqual({});
  });
});

describe("POST /perfil/api/totp/recovery-codes", () => {
  it("401 sin sesión, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await recoveryPost({ locals: {}, request: { json: async () => ({}) } });
    expect(res.status).toBe(401);
  });

  it("allowlist: solo los dos factores viajan al gateway", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ recovery_codes: ["A", "B"] }),
    }));
    globalThis.fetch = fetchMock;

    await recoveryPost({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({
          step_up_totp_code: "123456",
          step_up_recovery_code: "ABCDE-FGHJK-MNPQR-STUVW",
          // Campos que un cliente manipulado podría intentar colar: el
          // `user_id` es el vector clásico (firmar/regenerar como otro).
          user_id: "44444444-4444-4444-4444-444444444444",
          is_root: true,
        }),
      },
    });

    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(Object.keys(body).sort()).toEqual(["step_up_recovery_code", "step_up_totp_code"]);
  });

  it("la respuesta con los códigos NO se puede cachear", async () => {
    globalThis.fetch = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ recovery_codes: ["A"] }),
    }));
    const res = await recoveryPost({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ step_up_totp_code: "123456" }) },
    });
    // Los códigos solo existen en esta respuesta; el `no-store` del backend no
    // viaja solo, SvelteKit construye una respuesta nueva.
    expect(res.headers.get("Cache-Control")).toBe("no-store");
  });

  it("un fallo de conexión da 503, no un 200 con lista vacía", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new Error("ECONNREFUSED");
    });
    const res = await recoveryPost({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ step_up_totp_code: "123456" }) },
    });
    expect(res.status).toBe(503);
  });
});

describe("DELETE /perfil/api/totp — código de recuperación", () => {
  it("reenvía step_up_recovery_code (la salida de quien perdió el teléfono)", async () => {
    const fetchMock = vi.fn(async () => ({
      ok: true,
      status: 200,
      json: async () => ({ estado: "revocado" }),
    }));
    globalThis.fetch = fetchMock;

    await revokeDelete({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ step_up_recovery_code: "ABCDE-FGHJK-MNPQR-STUVW" }) },
    });

    const body = JSON.parse(fetchMock.mock.calls[0][1].body);
    expect(body.step_up_recovery_code).toBe("ABCDE-FGHJK-MNPQR-STUVW");
  });
});
