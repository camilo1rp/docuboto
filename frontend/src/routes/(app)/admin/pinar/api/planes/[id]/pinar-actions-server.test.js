// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * pinar-actions-server.test.js — Proxies de ciclo de vida y tablero de un
 * plan PINAR: aprobar/ejecutar/cerrar/tablero (D-05, RF-ADM-08, Fase 7).
 *
 * Cubre: guard 401, validación de id (encodeURIComponent + formato UUID),
 * validación del acto_administrativo obligatorio en aprobar, y propagación
 * de conflictos 409 del backend (ej. invariante "un solo plan en ejecución").
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { POST as aprobar } from "./aprobar/+server.js";
import { POST as ejecutar } from "./ejecutar/+server.js";
import { POST as cerrar } from "./cerrar/+server.js";
import { GET as tablero } from "./tablero/+server.js";

const PLAN_ID = "55555555-5555-5555-5555-555555555555";
const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("POST /admin/pinar/api/planes/[id]/aprobar", () => {
  it("401 sin sesión", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await aprobar({
      locals: {},
      params: { id: PLAN_ID },
      request: { json: async () => ({ acto_administrativo: "Resolución 001" }) },
    });
    expect(res.status).toBe(401);
  });

  it("id inválido: 400, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await aprobar({
      locals: SESSION_LOCALS,
      params: { id: "no-es-uuid" },
      request: { json: async () => ({ acto_administrativo: "Resolución 001" }) },
    });
    expect(res.status).toBe(400);
  });

  it("sin acto_administrativo: 400, sin llamar al gateway (la spec lo exige para aprobar)", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await aprobar({
      locals: SESSION_LOCALS,
      params: { id: PLAN_ID },
      request: { json: async () => ({ acto_administrativo: "   " }) },
    });
    expect(res.status).toBe(400);
  });

  it("escapa el id en la URL y reenvía solo acto_administrativo", async () => {
    let capturedUrl;
    let capturedBody;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedUrl = String(input);
      capturedBody = JSON.parse(init.body);
      return new Response(JSON.stringify({ id: PLAN_ID, estado: "aprobado" }), { status: 200 });
    });

    const res = await aprobar({
      locals: SESSION_LOCALS,
      params: { id: PLAN_ID },
      request: { json: async () => ({ acto_administrativo: "  Resolución 001 de 2026  " }) },
    });

    expect(res.status).toBe(200);
    expect(capturedUrl).toBe(`http://api-gateway:8080/api/v1/pinar/planes/${PLAN_ID}/aprobar`);
    expect(capturedBody).toEqual({ acto_administrativo: "Resolución 001 de 2026" });
  });

  it("propaga el 409 del backend (ej. el plan ya no está en 'borrador')", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(
          JSON.stringify({ error: "transicion_invalida", detail: "Solo un plan en 'borrador' puede aprobarse." }),
          { status: 409 },
        ),
    );
    const res = await aprobar({
      locals: SESSION_LOCALS,
      params: { id: PLAN_ID },
      request: { json: async () => ({ acto_administrativo: "Resolución 001" }) },
    });
    expect(res.status).toBe(409);
  });
});

describe("POST /admin/pinar/api/planes/[id]/ejecutar", () => {
  it("401 sin sesión", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await ejecutar({ locals: {}, params: { id: PLAN_ID } });
    expect(res.status).toBe(401);
  });

  it("id inválido: 400", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await ejecutar({ locals: SESSION_LOCALS, params: { id: "no-es-uuid" } });
    expect(res.status).toBe(400);
  });

  it("propaga el 409 'plan_en_ejecucion_existe' (invariante 1)", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(
          JSON.stringify({
            error: "plan_en_ejecucion_existe",
            detail: "Ya existe un PINAR en ejecución; ciérrelo antes de ejecutar otra versión.",
          }),
          { status: 409 },
        ),
    );
    const res = await ejecutar({ locals: SESSION_LOCALS, params: { id: PLAN_ID } });
    expect(res.status).toBe(409);
    const body = await res.json();
    expect(body.error).toBe("plan_en_ejecucion_existe");
  });

  it("escapa el id en la URL del gateway", async () => {
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input) => {
      capturedUrl = String(input);
      return new Response(JSON.stringify({ id: PLAN_ID, estado: "en_ejecucion" }), { status: 200 });
    });
    await ejecutar({ locals: SESSION_LOCALS, params: { id: PLAN_ID } });
    expect(capturedUrl).toBe(`http://api-gateway:8080/api/v1/pinar/planes/${PLAN_ID}/ejecutar`);
  });
});

describe("POST /admin/pinar/api/planes/[id]/cerrar", () => {
  it("401 sin sesión", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await cerrar({ locals: {}, params: { id: PLAN_ID } });
    expect(res.status).toBe(401);
  });

  it("id inválido: 400", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await cerrar({ locals: SESSION_LOCALS, params: { id: "no-es-uuid" } });
    expect(res.status).toBe(400);
  });

  it("escapa el id y llama al endpoint correcto", async () => {
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input) => {
      capturedUrl = String(input);
      return new Response(JSON.stringify({ id: PLAN_ID, estado: "cerrado" }), { status: 200 });
    });
    const res = await cerrar({ locals: SESSION_LOCALS, params: { id: PLAN_ID } });
    expect(res.status).toBe(200);
    expect(capturedUrl).toBe(`http://api-gateway:8080/api/v1/pinar/planes/${PLAN_ID}/cerrar`);
  });
});

describe("GET /admin/pinar/api/planes/[id]/tablero", () => {
  it("401 sin sesión", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await tablero({ locals: {}, params: { id: PLAN_ID } });
    expect(res.status).toBe(401);
  });

  it("id inválido: 400", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await tablero({ locals: SESSION_LOCALS, params: { id: "no-es-uuid" } });
    expect(res.status).toBe(400);
  });

  it("propaga el TableroResponse del backend", async () => {
    const payload = { plan_id: PLAN_ID, por_objetivo: [], por_eje: [] };
    globalThis.fetch = vi.fn(async () => new Response(JSON.stringify(payload), { status: 200 }));
    const res = await tablero({ locals: SESSION_LOCALS, params: { id: PLAN_ID } });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(payload);
  });
});
