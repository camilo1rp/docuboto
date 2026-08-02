// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * tracking-server.test.js — Proxy /envios/api/[id]/tracking (D-05, F5, Fase 8).
 *
 * Cubre: guard 401, validación de UUID en [id] (encodeURIComponent implícito),
 * paso de la respuesta upstream y el 503 explícito ante fallo de conexión.
 */
import { describe, it, expect, afterEach, vi } from "vitest";
import { GET as tracking } from "./+server.js";

const SHIPMENT_ID = "22222222-2222-2222-2222-222222222222";
const SESSION_LOCALS = {
  accessToken: "tok-abc",
  user: { tenant_slug: "icetex", sub: "33333333-3333-3333-3333-333333333333" },
};

afterEach(() => {
  vi.restoreAllMocks();
});

describe("envios/api/[id]/tracking", () => {
  it("401 sin sesión, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await tracking({ locals: {}, params: { id: SHIPMENT_ID } });
    expect(res.status).toBe(401);
  });

  it("400 con id no-UUID, sin llamar al gateway", async () => {
    globalThis.fetch = vi.fn(() => {
      throw new Error("no debió invocarse");
    });
    const res = await tracking({ locals: SESSION_LOCALS, params: { id: "no-es-uuid" } });
    expect(res.status).toBe(400);
  });

  it("encodeURIComponent del id en la URL upstream", async () => {
    let capturedUrl;
    globalThis.fetch = vi.fn(async (url) => {
      capturedUrl = url;
      return new Response(JSON.stringify({ shipment_id: SHIPMENT_ID }), { status: 200 });
    });
    await tracking({ locals: SESSION_LOCALS, params: { id: SHIPMENT_ID } });
    expect(capturedUrl).toContain(`/envios/${encodeURIComponent(SHIPMENT_ID)}/tracking`);
  });

  it("reenvía la respuesta upstream tal cual (incl. operador_conectado)", async () => {
    const upstream = {
      shipment_id: SHIPMENT_ID,
      operador: "4-72",
      guia: "G-1",
      estado_interno_actual: "en_transito",
      estado_operador: "EN REPARTO",
      estado_interno_mapeado: "en_transito",
      operador_conectado: false,
    };
    globalThis.fetch = vi.fn(async () => new Response(JSON.stringify(upstream), { status: 200 }));
    const res = await tracking({ locals: SESSION_LOCALS, params: { id: SHIPMENT_ID } });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(upstream);
  });

  it("503 explícito ante fallo de conexión (nunca un estado sintético)", async () => {
    globalThis.fetch = vi.fn(async () => {
      throw new Error("ECONNREFUSED");
    });
    const res = await tracking({ locals: SESSION_LOCALS, params: { id: SHIPMENT_ID } });
    expect(res.status).toBe(503);
    expect((await res.json()).error).toBe("connection_error");
  });
});
