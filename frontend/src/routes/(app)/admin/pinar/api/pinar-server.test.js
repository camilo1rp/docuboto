// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * pinar-server.test.js — Proxies /admin/pinar/api/ejes y /admin/pinar/api/planes
 * (D-05, RF-ADM-08, Fase 7).
 *
 * Cubre: guard 401, forward del filtro `estado`, allowlist anti mass-assignment
 * en POST /planes (nunca id/estado/aprobado_por viajan desde el cliente),
 * validación de fechas de vigencia, y propagación de status de error.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { GET as listEjes } from "./ejes/+server.js";
import { GET as listPlanes, POST as createPlan } from "./planes/+server.js";

const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("GET /admin/pinar/api/ejes", () => {
  it("401 sin sesión, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("fetch no debió invocarse sin sesión");
    });
    const res = await listEjes({ locals: {} });
    expect(res.status).toBe(401);
  });

  it("propaga el catálogo de ejes", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify([{ code: 1, nombre: "Gestión documental" }]), { status: 200 }),
    );
    const res = await listEjes({ locals: SESSION_LOCALS });
    expect(res.status).toBe(200);
    const body = await res.json();
    expect(body).toEqual([{ code: 1, nombre: "Gestión documental" }]);
  });
});

describe("GET /admin/pinar/api/planes", () => {
  it("401 sin sesión, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await listPlanes({ locals: {}, url: new URL("http://x/") });
    expect(res.status).toBe(401);
  });

  it("reenvía el filtro estado cuando es válido", async () => {
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input) => {
      capturedUrl = String(input);
      return new Response(JSON.stringify([]), { status: 200 });
    });
    const res = await listPlanes({
      locals: SESSION_LOCALS,
      url: new URL("http://x/?estado=aprobado"),
    });
    expect(res.status).toBe(200);
    expect(capturedUrl).toContain("estado=aprobado");
  });

  it("ignora un estado no reconocido (no lo reenvía)", async () => {
    let capturedUrl;
    globalThis.fetch = vi.fn(async (input) => {
      capturedUrl = String(input);
      return new Response(JSON.stringify([]), { status: 200 });
    });
    await listPlanes({
      locals: SESSION_LOCALS,
      url: new URL("http://x/?estado=DROP TABLE"),
    });
    expect(capturedUrl).not.toContain("estado=");
  });

  it("propaga status de error del gateway", async () => {
    globalThis.fetch = vi.fn(
      async () =>
        new Response(JSON.stringify({ error: "forbidden", status: 403 }), { status: 403 }),
    );
    const res = await listPlanes({ locals: SESSION_LOCALS, url: new URL("http://x/") });
    expect(res.status).toBe(403);
  });
});

describe("POST /admin/pinar/api/planes", () => {
  it("401 sin sesión", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await createPlan({ locals: {}, request: { json: async () => ({}) } });
    expect(res.status).toBe(401);
  });

  it("vigencia_desde inválida: 400, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await createPlan({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ vigencia_desde: "no-es-fecha" }) },
    });
    expect(res.status).toBe(400);
  });

  it("acto_administrativo demasiado largo: 400, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await createPlan({
      locals: SESSION_LOCALS,
      request: { json: async () => ({ acto_administrativo: "x".repeat(201) }) },
    });
    expect(res.status).toBe(400);
  });

  it("allowlist: solo campos de PlanCreate viajan; id/estado/aprobado_por NUNCA (mass-assignment)", async () => {
    let capturedBody;
    let capturedHeaders;
    globalThis.fetch = vi.fn(async (input, init) => {
      capturedBody = JSON.parse(init.body);
      capturedHeaders = init.headers;
      return new Response(JSON.stringify({ id: "p-1" }), { status: 201 });
    });

    const res = await createPlan({
      locals: SESSION_LOCALS,
      request: {
        json: async () => ({
          vision: "  Visión institucional  ",
          vigencia_desde: "2026-01-01",
          vigencia_hasta: "2030-01-01",
          // Campos ajenos que un cliente malicioso podría intentar colar:
          id: "otro-id",
          estado: "aprobado",
          aprobado_por: "44444444-4444-4444-4444-444444444444",
          version: 99,
        }),
      },
    });

    expect(res.status).toBe(201);
    expect(capturedBody).toEqual({
      vision: "Visión institucional",
      vigencia_desde: "2026-01-01",
      vigencia_hasta: "2030-01-01",
      acto_administrativo: null,
    });
    expect(capturedBody.estado).toBeUndefined();
    expect(capturedBody.id).toBeUndefined();
    expect(capturedHeaders["X-Tenant-Slug"]).toBe("icetex");
    expect(capturedHeaders["X-User-Id"]).toBe(SESSION_LOCALS.user.sub);
  });
});
