// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * trd-circuito-server.test.js — Proxies del circuito de convalidación TRD
 * (D-05, ADR-025/ADR-026): GET/POST .../versiones y los cinco actos.
 *
 * ADR-026 pidió el gemelo `GET /trd/{code}/versiones` (declarado por
 * ADR-025 y nunca implementado) — este archivo cubre esos proxies nuevos,
 * espejo del que ya existe para TVD.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { GET as getVersiones, POST as createVersion } from "./+server.js";
import { POST as aprobar } from "./[version]/aprobar/+server.js";
import { POST as devolver } from "./[version]/devolver/+server.js";
import { POST as registrarRusd } from "./[version]/registrar-rusd/+server.js";
import { POST as convalidar } from "./[version]/convalidar/+server.js";
import { POST as derogar } from "./[version]/derogar/+server.js";

const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("GET /admin/trd/api/[code]/versiones", () => {
  it("401 sin sesión, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await getVersiones({ locals: {}, params: { code: "OF" } });
    expect(res.status).toBe(401);
  });

  it("escapa el code en la URL del gateway", async () => {
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input) => {
      capturedUrl = String(input);
      return new Response(JSON.stringify([]), { status: 200 });
    });
    const res = await getVersiones({ locals: SESSION_LOCALS, params: { code: "OF/2026" } });
    expect(res.status).toBe(200);
    expect(capturedUrl).toContain(`/trd/${encodeURIComponent("OF/2026")}/versiones`);
  });
});

describe("POST /admin/trd/api/[code]/versiones (nueva versión en borrador)", () => {
  it("401 sin sesión, sin invocar request.json()", async () => {
    const request = {
      json: () => {
        throw new Error("request.json() no debió invocarse sin sesión");
      },
    };
    const res = await createVersion({ locals: {}, params: { code: "OF" }, request });
    expect(res.status).toBe(401);
  });

  it("clona con overrides parciales; solo campos de TrdSerieVersionCreate viajan (nunca retention_years/total_retention, id ni estado)", async () => {
    let capturedBody;
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ id: "s-2", version: 2 }), { status: 201 });
    });
    const res = await createVersion({
      locals: SESSION_LOCALS,
      params: { code: "OF" },
      request: {
        json: async () => ({
          name: "Serie nueva",
          archivo_gestion_years: 2,
          total_retention: 15,
          id: "otro-id",
          estado: "convalidada",
        }),
      },
    });
    expect(res.status).toBe(201);
    expect(capturedUrl).toContain("/trd/OF/versiones");
    expect(capturedBody).toEqual({ name: "Serie nueva", archivo_gestion_years: 2 });
  });
});

const ACTIONS = [
  {
    name: "aprobar",
    handler: () => aprobar,
    validBody: { acta_comite: "Acta 001-2026", fecha_aprobacion_comite: "2026-06-01" },
    invalidBodies: [{}],
  },
  {
    name: "devolver",
    handler: () => devolver,
    validBody: { motivo: "Falta soporte" },
    invalidBodies: [{}],
  },
  {
    name: "registrar-rusd",
    handler: () => registrarRusd,
    validBody: { fecha_publicacion: "2026-08-01" },
    invalidBodies: [{}],
  },
  {
    name: "convalidar",
    handler: () => convalidar,
    validBody: {
      acto_administrativo: "Resolución 045 de 2026",
      fecha_convalidacion: "2026-07-01",
      instancia_convalidante: "Consejo Departamental de Archivos",
    },
    invalidBodies: [{}],
  },
  {
    name: "derogar",
    handler: () => derogar,
    validBody: null,
    invalidBodies: [],
  },
];

for (const action of ACTIONS) {
  describe(`POST /admin/trd/api/[code]/versiones/[version]/${action.name}`, () => {
    it("401 sin sesión, sin llamar al gateway", async () => {
      globalThis.fetch = vi.fn(() => {
        throw new Error("no debió invocarse");
      });
      const res = await action.handler()({
        locals: {},
        params: { code: "OF", version: "1" },
        request: { json: async () => action.validBody ?? {} },
      });
      expect(res.status).toBe(401);
    });

    it("versión inválida: 400, sin llamar al gateway", async () => {
      globalThis.fetch = vi.fn(() => {
        throw new Error("no debió invocarse");
      });
      const res = await action.handler()({
        locals: SESSION_LOCALS,
        params: { code: "OF", version: "0" },
        request: { json: async () => action.validBody ?? {} },
      });
      expect(res.status).toBe(400);
    });

    for (const invalidBody of action.invalidBodies) {
      it(`body inválido (${JSON.stringify(invalidBody)}): 400, sin llamar al gateway`, async () => {
        globalThis.fetch = vi.fn(() => {
          throw new Error("no debió invocarse");
        });
        const res = await action.handler()({
          locals: SESSION_LOCALS,
          params: { code: "OF", version: "1" },
          request: { json: async () => invalidBody },
        });
        expect(res.status).toBe(400);
      });
    }

    it("escapa code y version en la URL del gateway", async () => {
      let capturedUrl;
      globalThis.fetch = vi.fn(async (input) => {
        capturedUrl = String(input);
        return new Response(JSON.stringify({ id: "s-1", estado: "aprobada" }), { status: 200 });
      });
      const res = await action.handler()({
        locals: SESSION_LOCALS,
        params: { code: "OF/2026", version: "1" },
        request: { json: async () => action.validBody ?? {} },
      });
      expect(res.status).toBe(200);
      expect(capturedUrl).toContain(
        `/trd/${encodeURIComponent("OF/2026")}/versiones/1/${action.name}`,
      );
    });
  });
}
