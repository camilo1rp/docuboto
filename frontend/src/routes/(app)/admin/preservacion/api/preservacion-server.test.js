// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * preservacion-server.test.js — Proxies BFF de /admin/preservacion/api
 * (D-05, E10, Fase 4). Cubre: guard 401, allowlist del plan (PUT), límites
 * de paginación de los listados operacionales, y escapado del
 * expediente_id en el reintento de sellado.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { GET as getPlan, PUT as putPlan } from "./plan/+server.js";
import { GET as getVersions } from "./plan/versions/+server.js";
import { GET as getPendientesFirma } from "./indices/pendientes-firma/+server.js";
import { GET as getPendientesRenovacion } from "./indices/pendientes-renovacion/+server.js";
import { POST as postFirmar } from "./indices/[expedienteId]/firmar/+server.js";

const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "77777777-7777-7777-7777-777777777777" },
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("GET /admin/preservacion/api/plan", () => {
  it("401 sin sesión, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await getPlan({ locals: {} });
    expect(res.status).toBe(401);
  });

  it("propaga el 404 (sin plan) del gateway tal cual", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ error: "plan_not_found", status: 404 }), { status: 404 }),
    );
    const res = await getPlan({ locals: SESSION_LOCALS });
    expect(res.status).toBe(404);
  });
});

describe("PUT /admin/preservacion/api/plan", () => {
  it("401 sin sesión, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await putPlan({ locals: {}, request: { json: async () => ({}) } });
    expect(res.status).toBe(401);
  });

  it("allowlist: solo los campos del schema viajan, nunca el body íntegro", async () => {
    globalThis.fetch = vi.fn(async (url, opts) => {
      const body = JSON.parse(opts.body);
      expect(body).toEqual({
        formatos_destino: ["pdf/a-2b", "tiff"],
        formatos_origen: ["application/msword"],
        num_copias: 2,
        periodicidad_fixity_dias: 90,
        politica_migracion: "migrar cada 5 años",
        contingencia: "sitio alterno",
      });
      return new Response(JSON.stringify({ version: 2 }), { status: 201 });
    });
    const res = await putPlan({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({
          formatos_destino: ["pdf/a-2b", " tiff "],
          formatos_origen: [" Application/MSWord ", "application/msword"],
          num_copias: "2",
          periodicidad_fixity_dias: "90",
          politica_migracion: "migrar cada 5 años",
          contingencia: "sitio alterno",
          // Campo ajeno al schema — NUNCA debe viajar.
          creado_por: "usuario-suplantado",
        }),
      },
    });
    expect(res.status).toBe(201);
  });

  it("un MIME mal escrito es 422, sin llamar al gateway", async () => {
    // Un "msword" a secas no casa con ningún archivo: el barrido no
    // convertiría nada y la política aparecería como declarada en la pantalla.
    // Lo peligroso aquí no es convertir de más, es creer que se convirtió.
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse con un MIME inválido");
    });
    const res = await putPlan({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ formatos_origen: ["msword"] }) },
    });
    expect(res.status).toBe(422);
  });

  it("num_copias < 1: 422 validation_error, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse con num_copias inválido");
    });
    const res = await putPlan({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ num_copias: 0 }) },
    });
    expect(res.status).toBe(422);
  });
});

describe("GET /admin/preservacion/api/plan/versions", () => {
  it("401 sin sesión", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await getVersions({ locals: {} });
    expect(res.status).toBe(401);
  });
});

describe("GET /admin/preservacion/api/indices/pendientes-firma", () => {
  it("401 sin sesión, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await getPendientesFirma({ locals: {}, url: new URL("http://x/") });
    expect(res.status).toBe(401);
  });

  it("clampa size a 100 como máximo (mismo límite que el backend)", async () => {
    globalThis.fetch = vi.fn(async (url) => {
      expect(url).toContain("size=100");
      return new Response(JSON.stringify([]), { status: 200 });
    });
    await getPendientesFirma({
      locals: SESSION_LOCALS,
      url: new URL("http://x/?size=9999"),
    });
  });

  it("propaga X-Total-Count del gateway", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify([]), { status: 200, headers: { "X-Total-Count": "5" } }),
    );
    const res = await getPendientesFirma({ locals: SESSION_LOCALS, url: new URL("http://x/") });
    expect(res.headers.get("X-Total-Count")).toBe("5");
  });
});

describe("GET /admin/preservacion/api/indices/pendientes-renovacion", () => {
  it("401 sin sesión", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await getPendientesRenovacion({ locals: {}, url: new URL("http://x/") });
    expect(res.status).toBe(401);
  });
});

describe("POST /admin/preservacion/api/indices/[expedienteId]/firmar", () => {
  const EXP_ID = "expediente-1?x=1";

  it("401 sin sesión, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await postFirmar({ locals: {}, params: { expedienteId: EXP_ID } });
    expect(res.status).toBe(401);
  });

  it("escapa el expedienteId en la URL del gateway", async () => {
    globalThis.fetch = vi.fn(async (url, opts) => {
      expect(url).toContain(encodeURIComponent(EXP_ID));
      expect(url).not.toContain("?x=1");
      expect(opts.method).toBe("POST");
      return new Response(JSON.stringify({ estado: "firmado" }), { status: 200 });
    });
    const res = await postFirmar({ locals: SESSION_LOCALS, params: { expedienteId: EXP_ID } });
    expect(res.status).toBe(200);
  });

  it("409 idempotente (ya firmado) se propaga tal cual", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ error: "indice_ya_firmado", status: 409 }), { status: 409 }),
    );
    const res = await postFirmar({ locals: SESSION_LOCALS, params: { expedienteId: "e1" } });
    expect(res.status).toBe(409);
  });
});
