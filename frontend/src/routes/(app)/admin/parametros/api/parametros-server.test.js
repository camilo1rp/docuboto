// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * parametros-server.test.js — Proxies /admin/parametros/api/* (D-05, E14).
 *
 * Cubre: guard 401, validación de fecha/rango, allowlist ({value} solo en
 * PUT de params — el key SIEMPRE viene de la ruta, nunca del body),
 * encodeURIComponent de [key]/[id], y límites del rango de días hábiles.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { GET as getParam, PUT as putParam } from "./params/[key]/+server.js";
import { GET as listHolidays, POST as createHoliday } from "./holidays/+server.js";
import { DELETE as deleteHoliday } from "./holidays/[id]/+server.js";
import { POST as seedHolidays } from "./holidays/seed/+server.js";
import { GET as calcBusinessDays } from "./business-days/+server.js";

const HOLIDAY_ID = "44444444-4444-4444-4444-444444444444";
const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("GET/PUT /admin/parametros/api/params/[key]", () => {
  it("GET: 401 sin sesión", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await getParam({ locals: {}, params: { key: "plazo_dias" } });
    expect(res.status).toBe(401);
  });

  it("PUT: sin 'value' en el body -> 400, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await putParam({
      locals: SESSION_LOCALS,
      params: { key: "plazo_dias" },
      request: { json: async () => ({}) },
    });
    expect(res.status).toBe(400);
  });

  it("PUT: escapa la clave en la URL del gateway y solo reenvía {value}", async () => {
    let capturedUrl;
    let capturedBody;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ key: "plazo dias/x", value: 15 }), { status: 200 });
    });
    const res = await putParam({
      locals: SESSION_LOCALS,
      params: { key: "plazo dias/x" },
      request: { json: async () => ({ value: 15, key: "otro-key-inyectado" }) },
    });
    expect(res.status).toBe(200);
    expect(capturedUrl).toContain(`/config/params/${encodeURIComponent("plazo dias/x")}`);
    expect(capturedBody).toEqual({ value: 15 });
  });
});

describe("GET/POST /admin/parametros/api/holidays", () => {
  it("GET: 401 sin sesión", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await listHolidays({ locals: {}, url: new URL("http://x/") });
    expect(res.status).toBe(401);
  });

  it("POST: fecha con formato inválido -> 400, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await createHoliday({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ fecha: "01/01/2026", descripcion: "Año nuevo" }) },
    });
    expect(res.status).toBe(400);
  });

  it("POST: allowlist anti mass-assignment", async () => {
    let capturedBody;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ id: HOLIDAY_ID }), { status: 201 });
    });
    const res = await createHoliday({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({
          fecha: "2026-01-01",
          descripcion: "Año Nuevo",
          id: "otro-id",
          tenant_slug: "otro-tenant",
        }),
      },
    });
    expect(res.status).toBe(201);
    expect(capturedBody).toEqual({
      fecha: "2026-01-01",
      descripcion: "Año Nuevo",
      tipo: "festivo",
      is_national: true,
    });
  });
});

describe("DELETE /admin/parametros/api/holidays/[id]", () => {
  it("id no-UUID -> 400, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await deleteHoliday({ locals: SESSION_LOCALS, params: { id: "no-es-uuid" } });
    expect(res.status).toBe(400);
  });

  it("204 del gateway se propaga sin cuerpo", async () => {
    globalThis.fetch = vi.fn(async () => new Response(null, { status: 204 }));
    const res = await deleteHoliday({ locals: SESSION_LOCALS, params: { id: HOLIDAY_ID } });
    expect(res.status).toBe(204);
  });
});

describe("POST /admin/parametros/api/holidays/seed", () => {
  it("year_to < year_from -> 400, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await seedHolidays({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ year_from: 2027, year_to: 2026 }) },
    });
    expect(res.status).toBe(400);
  });
});

describe("GET /admin/parametros/api/business-days", () => {
  it("401 sin sesión", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await calcBusinessDays({
      locals: {},
      url: new URL("http://x/?from=2026-01-01&days=10"),
    });
    expect(res.status).toBe(401);
  });

  it("days negativo -> 400, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await calcBusinessDays({
      locals: SESSION_LOCALS,
      url: new URL("http://x/?from=2026-01-01&days=-1"),
    });
    expect(res.status).toBe(400);
  });

  it("days > 3650 -> 400, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await calcBusinessDays({
      locals: SESSION_LOCALS,
      url: new URL("http://x/?from=2026-01-01&days=99999"),
    });
    expect(res.status).toBe(400);
  });

  it("propaga el resultado del gateway", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(
          JSON.stringify({ from_date: "2026-01-01", business_days: 10, due_date: "2026-01-16" }),
          {
            status: 200,
          },
        ),
    );
    const res = await calcBusinessDays({
      locals: SESSION_LOCALS,
      url: new URL("http://x/?from=2026-01-01&days=10"),
    });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({
      from_date: "2026-01-01",
      business_days: 10,
      due_date: "2026-01-16",
    });
  });
});

describe("PUT /admin/parametros/api/params/[key] — motivo", () => {
  it("reenvía `motivo` recortado y nada más", async () => {
    let enviado;
    globalThis.fetch = vi.fn(async (_u, init) => {
      enviado = JSON.parse(init.body);
      return new Response("{}", { status: 200 });
    });
    await putParam({
      locals: SESSION_LOCALS,
      params: { key: "firma_personal_require_xades" },
      request: {
        json: async () => ({
          value: "clasificados",
          motivo: "  Falta enrolar certificados.  ",
          key: "otra",
        }),
      },
    });
    expect(enviado).toEqual({ value: "clasificados", motivo: "Falta enrolar certificados." });
  });

  it("sin motivo no lo inventa", async () => {
    let enviado;
    globalThis.fetch = vi.fn(async (_u, init) => {
      enviado = JSON.parse(init.body);
      return new Response("{}", { status: 200 });
    });
    await putParam({
      locals: SESSION_LOCALS,
      params: { key: "plazo_dias" },
      request: { json: async () => ({ value: 5, motivo: "   " }) },
    });
    expect(enviado).toEqual({ value: 5 });
  });
});
