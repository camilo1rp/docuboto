// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * signing-key-server.test.js — Proxies BFF de /perfil/api/signing-key
 * (D-05, E17/F4). Cubre: guard 401, allowlist, validación del step-up TOTP
 * (SIEMPRE requerido para auto-revocar, ver `SigningKeyService.revoke_self`),
 * y que la clave privada nunca aparece en ningún request/response reenviado.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { GET as statusGet, DELETE as revokeDelete } from "./+server.js";
import { POST as enrollPost } from "./enroll/+server.js";

const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("GET /perfil/api/signing-key", () => {
  it("401 sin locals.accessToken, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await statusGet({ locals: {} });
    expect(res.status).toBe(401);
  });

  it("reenvía identidad y marca Cache-Control: no-store", async () => {
    let capturedHeaders;
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedHeaders = init.headers;
      return new Response(JSON.stringify({ estado: "activo", cert_pem: "-----BEGIN CERT-----" }), {
        status: 200,
      });
    });
    const res = await statusGet({ locals: SESSION_LOCALS });
    expect(res.status).toBe(200);
    expect(capturedUrl).toContain("/auth/me/signing-key");
    expect(capturedHeaders.Authorization).toBe("Bearer tok-abc");
    expect(capturedHeaders["X-User-Id"]).toBe(SESSION_LOCALS.user.sub);
    expect(res.headers.get("Cache-Control")).toBe("no-store");
  });
});

describe("POST /perfil/api/signing-key/enroll", () => {
  it("401 sin locals.accessToken, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await enrollPost({ locals: {}, request: { json: async () => ({}) } });
    expect(res.status).toBe(401);
  });

  it("allowlist: solo step_up_totp_code viaja; el body nunca lleva material de clave", async () => {
    let capturedBody;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedBody = JSON.parse(init.body);
      return new Response(
        JSON.stringify({
          estado: "pendiente_emision",
          csr_pem: "-----BEGIN CERTIFICATE REQUEST-----",
        }),
        { status: 201 },
      );
    });
    const res = await enrollPost({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({
          private_key: "no-debería-existir-jamás",
          user_id: "otro-usuario",
        }),
      },
    });
    expect(res.status).toBe(201);
    expect(capturedBody).toEqual({});
    const data = await res.json();
    expect(data).not.toHaveProperty("private_key");
    expect(data.csr_pem).toMatch(/CERTIFICATE REQUEST/);
  });

  it.each([["12345"], ["abcdef"]])(
    "step_up_totp_code=%j con forma inválida: 422 validation_error, sin llamar al gateway",
    async (code) => {
      globalThis.fetch = vi.fn(() => {
        throw new Error("fetch no debió invocarse con step-up inválido");
      });
      const res = await enrollPost({
        locals: SESSION_LOCALS,
        request: { json: async () => ({ step_up_totp_code: code }) },
      });
      expect(res.status).toBe(422);
      expect((await res.json()).error).toBe("validation_error");
    },
  );

  it("step_up_totp_code='' (cadena vacía) se trata como ausente — payload vacío, sin 422", async () => {
    let capturedBody;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ estado: "pendiente_emision", csr_pem: "csr" }), {
        status: 201,
      });
    });
    const res = await enrollPost({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ step_up_totp_code: "" }) },
    });
    expect(res.status).toBe(201);
    expect(capturedBody).toEqual({});
  });

  it("con step_up_totp_code válido (re-enroll sobre credencial activa), se reenvía", async () => {
    let capturedBody;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ estado: "pendiente_emision", csr_pem: "csr" }), {
        status: 201,
      });
    });
    await enrollPost({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ step_up_totp_code: "654321" }) },
    });
    expect(capturedBody).toEqual({ step_up_totp_code: "654321" });
  });
});

describe("DELETE /perfil/api/signing-key (auto-revocación)", () => {
  it("401 sin locals.accessToken, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await revokeDelete({
      locals: {},
      request: { json: async () => ({ step_up_totp_code: "123456" }) },
    });
    expect(res.status).toBe(401);
  });

  it.each([[undefined], [null], [""], ["12345"], ["abcdef"]])(
    "step_up_totp_code=%j — SIEMPRE requerido con forma válida: 422, sin llamar al gateway",
    async (code) => {
      globalThis.fetch = vi.fn(() => {
        throw new Error("fetch no debió invocarse sin step-up válido");
      });
      const res = await revokeDelete({
        locals: SESSION_LOCALS,
        request: { json: async () => ({ step_up_totp_code: code }) },
      });
      expect(res.status).toBe(422);
      expect((await res.json()).error).toBe("validation_error");
    },
  );

  it("allowlist: solo step_up_totp_code y motivo (del vocabulario cerrado) viajan", async () => {
    let capturedBody;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ estado: "revocado" }), { status: 200 });
    });
    const res = await revokeDelete({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({
          step_up_totp_code: "123456",
          motivo: "cessation_of_operation",
          user_id: "otro-usuario",
          cert_pem: "no-debería-reenviarse",
        }),
      },
    });
    expect(res.status).toBe(200);
    expect(capturedBody).toEqual({ step_up_totp_code: "123456", motivo: "cessation_of_operation" });
  });

  it("un motivo fuera del vocabulario cerrado no viaja (texto libre descartado)", async () => {
    let capturedBody;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ estado: "revocado" }), { status: 200 });
    });
    await revokeDelete({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ step_up_totp_code: "123456", motivo: "sospecha de compromiso" }) },
    });
    expect(capturedBody).toEqual({ step_up_totp_code: "123456" });
  });
});
