// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * tvd-circuito-server.test.js — Proxies del circuito de convalidación TVD
 * (D-05, ADR-026): GET/POST .../versiones y los cinco actos (aprobar,
 * devolver, registrar-rusd, convalidar, derogar).
 *
 * Cubre: guard 401 (antes de leer el body y antes de contactar al gateway),
 * validación de código/versión, allowlist/validación del body de cada
 * acción, y encodeURIComponent del `code` en la URL del gateway.
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
const CODE_WITH_SLASH_LIKE_CHARS = "FA/01";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("GET /admin/tvd/api/[code]/versiones", () => {
  it("401 sin sesión, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await getVersiones({ locals: {}, params: { code: "FA-01" } });
    expect(res.status).toBe(401);
  });

  it("escapa el code en la URL del gateway", async () => {
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input) => {
      capturedUrl = String(input);
      return new Response(JSON.stringify([]), { status: 200 });
    });
    const res = await getVersiones({
      locals: SESSION_LOCALS,
      params: { code: CODE_WITH_SLASH_LIKE_CHARS },
    });
    expect(res.status).toBe(200);
    expect(capturedUrl).toContain(
      `/tvd/${encodeURIComponent(CODE_WITH_SLASH_LIKE_CHARS)}/versiones`,
    );
    expect(capturedUrl).not.toContain("/tvd/FA/01/versiones");
  });
});

describe("POST /admin/tvd/api/[code]/versiones (nueva versión en borrador)", () => {
  it("401 sin sesión, sin invocar request.json()", async () => {
    const request = {
      json: () => {
        throw new Error("request.json() no debió invocarse sin sesión");
      },
    };
    const res = await createVersion({ locals: {}, params: { code: "FA-01" }, request });
    expect(res.status).toBe(401);
  });

  it("clona con overrides parciales; solo campos de TvdAgrupacionVersionCreate viajan", async () => {
    let capturedBody;
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ id: "tvd-2", version: 2 }), { status: 201 });
    });
    const res = await createVersion({
      locals: SESSION_LOCALS,
      params: { code: "FA-01" },
      request: {
        json: async () => ({
          justificacion_valoracion: "Nueva justificación",
          id: "otro-id",
          estado: "convalidada",
        }),
      },
    });
    expect(res.status).toBe(201);
    expect(capturedUrl).toContain("/tvd/FA-01/versiones");
    expect(capturedBody).toEqual({ justificacion_valoracion: "Nueva justificación" });
  });
});

const ACTIONS = [
  {
    name: "aprobar",
    handler: () => aprobar,
    validBody: { acta_comite: "Acta 001-2026", fecha_aprobacion_comite: "2026-06-01" },
    invalidBodies: [{}, { acta_comite: "Acta 001" }, { fecha_aprobacion_comite: "2026-06-01" }],
  },
  {
    name: "devolver",
    handler: () => devolver,
    validBody: { motivo: "Falta soporte documental del fondo" },
    invalidBodies: [{}, { motivo: "" }],
  },
  {
    name: "registrar-rusd",
    handler: () => registrarRusd,
    validBody: { rusd_radicado: "RUSD-2026-001" },
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
    invalidBodies: [{}, { acto_administrativo: "Resolución 045 de 2026" }],
  },
  {
    name: "derogar",
    handler: () => derogar,
    validBody: null,
    invalidBodies: [],
  },
];

for (const action of ACTIONS) {
  describe(`POST /admin/tvd/api/[code]/versiones/[version]/${action.name}`, () => {
    it("401 sin sesión, sin llamar al gateway", async () => {
      globalThis.fetch = vi.fn(() => {
        throw new Error("no debió invocarse");
      });
      const res = await action.handler()({
        locals: {},
        params: { code: "FA-01", version: "1" },
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
        params: { code: "FA-01", version: "no-es-numero" },
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
          params: { code: "FA-01", version: "1" },
          request: { json: async () => invalidBody },
        });
        expect(res.status).toBe(400);
      });
    }

    it("escapa code y version en la URL del gateway; envía el body construido", async () => {
      let capturedUrl;
      let capturedBody;
      globalThis.fetch = vi.fn(async (input, init) => {
        capturedUrl = String(input);
        capturedBody = JSON.parse(init.body);
        return new Response(JSON.stringify({ id: "tvd-1", estado: "aprobada" }), { status: 200 });
      });
      const res = await action.handler()({
        locals: SESSION_LOCALS,
        params: { code: CODE_WITH_SLASH_LIKE_CHARS, version: "1" },
        request: { json: async () => action.validBody ?? {} },
      });
      expect(res.status).toBe(200);
      expect(capturedUrl).toContain(
        `/tvd/${encodeURIComponent(CODE_WITH_SLASH_LIKE_CHARS)}/versiones/1/${action.name}`,
      );
      if (action.validBody) {
        for (const key of Object.keys(action.validBody)) {
          expect(capturedBody[key]).toBeDefined();
        }
      } else {
        expect(capturedBody).toEqual({});
      }
    });
  });
}
