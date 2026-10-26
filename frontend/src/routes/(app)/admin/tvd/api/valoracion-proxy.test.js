// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Proxy de la valoración del Comité sobre una TVD (C-5 / C7). Mutaciones que
 * deben caer: reenviar campos fuera de la allowlist; aceptar un fundamento
 * corto; omitir el guard de sesión.
 */
import { describe, it, expect, vi, beforeEach } from "vitest";
import { GET, POST } from "./[id]/valoracion/+server.js";

const ID = "7ce8f33d-0dd9-4ecc-9785-423f731032a8";
const LOCALS = { accessToken: "t", user: { tenant_slug: "demo", sub: "u1" } };
const ok = { acta_comite: "Acta 3", fecha_acta: "2026-03-01", fundamento: "x".repeat(20) };

beforeEach(() => {
  global.fetch = vi.fn().mockResolvedValue({ status: 201, json: async () => ({}) });
});

describe("valoración TVD proxy", () => {
  it("sin sesión: 401", async () => {
    expect((await GET({ locals: {}, params: { id: ID } })).status).toBe(401);
    expect(
      (await POST({ locals: {}, params: { id: ID }, request: { json: async () => ok } })).status,
    ).toBe(401);
  });

  it("POST con allowlist (control positivo)", async () => {
    await POST({
      locals: LOCALS,
      params: { id: ID },
      request: { json: async () => ({ ...ok, decidida_por: "intruso" }) },
    });
    expect(global.fetch.mock.calls[0][0]).toMatch(new RegExp(`/tvd/${ID}/valoracion$`));
    expect(JSON.parse(global.fetch.mock.calls[0][1].body)).toEqual(ok);
  });

  it("fundamento corto: 422 sin tocar el backend", async () => {
    const r = await POST({
      locals: LOCALS,
      params: { id: ID },
      request: { json: async () => ({ ...ok, fundamento: "corto" }) },
    });
    expect(r.status).toBe(422);
    expect(global.fetch).not.toHaveBeenCalled();
  });
});

describe("selección (disposición S) proxy", () => {
  const EXP = "8a4f0c1e-0dd9-4ecc-9785-423f731032a9";
  const ev = (body) => ({
    locals: LOCALS,
    params: { id: ID, expedienteId: EXP },
    request: { json: async () => body },
  });

  it("sin sesión: 401", async () => {
    const { POST } =
      await import("./[id]/valoracion/expedientes/[expedienteId]/seleccion/+server.js");
    expect(
      (await POST({ locals: {}, params: { id: ID, expedienteId: EXP }, request: ev({}).request }))
        .status,
    ).toBe(401);
  });

  it("reenvía solo la allowlist {accion, fundamento}", async () => {
    const { POST } =
      await import("./[id]/valoracion/expedientes/[expedienteId]/seleccion/+server.js");
    await POST(
      ev({ accion: "conservar", fundamento: "Entra a la muestra", decidida_por: "intruso" }),
    );
    expect(global.fetch.mock.calls[0][0]).toMatch(
      new RegExp(`/tvd/${ID}/valoracion/expedientes/${EXP}/seleccion$`),
    );
    expect(JSON.parse(global.fetch.mock.calls[0][1].body)).toEqual({
      accion: "conservar",
      fundamento: "Entra a la muestra",
    });
  });

  it("acción inválida o fundamento corto: 422 sin tocar el backend", async () => {
    const { POST } =
      await import("./[id]/valoracion/expedientes/[expedienteId]/seleccion/+server.js");
    expect((await POST(ev({ accion: "borrar", fundamento: "x".repeat(20) }))).status).toBe(422);
    expect((await POST(ev({ accion: "conservar", fundamento: "corto" }))).status).toBe(422);
    expect(global.fetch).not.toHaveBeenCalled();
  });

  it("identificador que no es UUID: 400", async () => {
    const { POST } =
      await import("./[id]/valoracion/expedientes/[expedienteId]/seleccion/+server.js");
    const r = await POST({
      locals: LOCALS,
      params: { id: ID, expedienteId: "../x" },
      request: ev({}).request,
    });
    expect(r.status).toBe(400);
  });
});
