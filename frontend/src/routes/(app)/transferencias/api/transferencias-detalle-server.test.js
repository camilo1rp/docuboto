// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * transferencias-detalle-server.test.js — Proxies BFF del detalle de una
 * transferencia (D-05, E12/E17/F4, Fase 4). Cubre: guard 401, escapado de
 * ids en la URL del gateway, allowlist de `acta/firmar-personal`, y el
 * paso a través de `verify=true` en `acta`.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { GET as getTransferencia } from "./[id]/+server.js";
import { GET as getActa } from "./[id]/acta/+server.js";
import { GET as getVerificacion } from "./[id]/acta/verificacion/+server.js";
import { POST as firmarPersonal } from "./[id]/acta/firmar-personal/+server.js";
import { POST as firmarInstitucional } from "./[id]/acta/firmar/+server.js";

const ID = "aaaaaaaa-1111-2222-3333-444444444444";
const ID_CON_INYECCION = "aaaaaaaa-1111-2222-3333-444444444444?x=1&y=2";
const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "99999999-9999-9999-9999-999999999999" },
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("GET /transferencias/api/[id]", () => {
  it("401 sin locals.accessToken, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await getTransferencia({ locals: {}, params: { id: ID } });
    expect(res.status).toBe(401);
  });

  it("escapa el id en la URL del gateway", async () => {
    globalThis.fetch = vi.fn(async (url) => {
      expect(url).toContain(encodeURIComponent(ID_CON_INYECCION));
      expect(url).not.toContain("?x=1&y=2");
      return new Response(JSON.stringify({ id: ID }), { status: 200 });
    });
    const res = await getTransferencia({
      locals: SESSION_LOCALS,
      params: { id: ID_CON_INYECCION },
    });
    expect(res.status).toBe(200);
  });
});

describe("GET /transferencias/api/[id]/acta", () => {
  it("401 sin sesión", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await getActa({ locals: {}, params: { id: ID }, url: new URL("http://x/acta") });
    expect(res.status).toBe(401);
  });

  it("verify=true se reenvía solo cuando el cliente lo pide explícitamente", async () => {
    globalThis.fetch = vi.fn(async (url) => {
      expect(url).toContain("verify=true");
      return new Response(JSON.stringify({}), { status: 200 });
    });
    await getActa({
      locals: SESSION_LOCALS,
      params: { id: ID },
      url: new URL("http://x/acta?verify=true"),
    });
  });

  it("sin verify=, no se agrega el query param", async () => {
    globalThis.fetch = vi.fn(async (url) => {
      expect(url).not.toContain("verify=");
      return new Response(JSON.stringify({}), { status: 200 });
    });
    await getActa({ locals: SESSION_LOCALS, params: { id: ID }, url: new URL("http://x/acta") });
  });
});

describe("GET /transferencias/api/[id]/acta/verificacion", () => {
  it("401 sin sesión", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await getVerificacion({ locals: {}, params: { id: ID } });
    expect(res.status).toBe(401);
  });
});

describe("POST /transferencias/api/[id]/acta/firmar-personal", () => {
  it("401 sin sesión, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await firmarPersonal({
      locals: {},
      params: { id: ID },
      request: { json: async () => ({ totp_code: "123456" }) },
    });
    expect(res.status).toBe(401);
  });

  it("totp_code vacío: 422 validation_error, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin totp_code");
    });
    const res = await firmarPersonal({
      locals: SESSION_LOCALS,
      params: { id: ID },
      request: { json: async () => ({ totp_code: "" }) },
    });
    expect(res.status).toBe(422);
    expect((await res.json()).error).toBe("validation_error");
  });

  it("allowlist: rol arbitrario no permitido se descarta; rol válido y totp_code viajan", async () => {
    globalThis.fetch = vi.fn(async (url, opts) => {
      expect(url).toContain(`/transferencias/${ID}/acta/firmar-personal`);
      const body = JSON.parse(opts.body);
      expect(body).toEqual({ totp_code: "654321", rol: "entrega" });
      return new Response(JSON.stringify({ rol: "entrega" }), { status: 201 });
    });
    const res = await firmarPersonal({
      locals: SESSION_LOCALS,
      params: { id: ID },
      request: {
        json: async () => ({
          totp_code: "654321",
          rol: "entrega",
          // Campos ajenos al schema NUNCA deben viajar (anti mass-assignment).
          firmante_id: "otro-usuario",
          acreditado: true,
        }),
      },
    });
    expect(res.status).toBe(201);
  });

  it("rol inválido se omite del payload en vez de reenviarse tal cual", async () => {
    globalThis.fetch = vi.fn(async (url, opts) => {
      const body = JSON.parse(opts.body);
      expect(body).toEqual({ totp_code: "111111" });
      return new Response(JSON.stringify({}), { status: 201 });
    });
    await firmarPersonal({
      locals: SESSION_LOCALS,
      params: { id: ID },
      request: { json: async () => ({ totp_code: "111111", rol: "administrador" }) },
    });
  });

  it("inyecta X-User-Id desde locals, nunca desde el body", async () => {
    globalThis.fetch = vi.fn(async (url, opts) => {
      expect(opts.headers["X-User-Id"]).toBe(SESSION_LOCALS.user.sub);
      return new Response(JSON.stringify({}), { status: 201 });
    });
    await firmarPersonal({
      locals: SESSION_LOCALS,
      params: { id: ID },
      request: {
        json: async () => ({ totp_code: "111111", firmante_id: "usuario-suplantado" }),
      },
    });
  });
});

describe("POST /transferencias/api/[id]/acta/firmar", () => {
  it("401 sin sesión, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await firmarInstitucional({ locals: {}, params: { id: ID } });
    expect(res.status).toBe(401);
  });

  it("reenvía al endpoint de reintento institucional, sin body", async () => {
    globalThis.fetch = vi.fn(async (url, opts) => {
      expect(url).toContain(`/transferencias/${ID}/acta/firmar`);
      expect(opts.method).toBe("POST");
      return new Response(JSON.stringify({ estado: "firmado" }), { status: 200 });
    });
    const res = await firmarInstitucional({ locals: SESSION_LOCALS, params: { id: ID } });
    expect(res.status).toBe(200);
  });
});
