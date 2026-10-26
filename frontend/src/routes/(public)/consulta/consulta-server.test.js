// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/** Consulta ciudadana por código: es una superficie SIN sesión, a propósito. */
import { describe, expect, it, vi } from "vitest";

vi.mock("$lib/utils/config.server.js", () => ({
  API_V1: "http://gw/api/v1",
  PUBLIC_TENANT: "demo",
}));

const { GET } = await import("./api/+server.js");

const CODIGO = "bde8e66dbee949b887f904033bb1fc4c";

function evento(codigo, respuesta) {
  return {
    url: new URL(`http://x/consulta/api?codigo=${codigo}`),
    fetch: vi.fn().mockResolvedValue(respuesta),
  };
}

describe("/consulta/api", () => {
  it("no exige sesión: consulta con solo el código", async () => {
    const ev = evento(CODIGO, {
      ok: true,
      status: 200,
      json: async () => ({ tracking_number: "2026-DEMO-E-000019", status: "registered" }),
    });
    const res = await GET(ev);
    expect(res.status).toBe(200);
    expect((await res.json()).tracking_number).toBe("2026-DEMO-E-000019");
    // El inquilino sale de la configuración de despliegue, no de la URL: a un
    // ciudadano no se le explica qué es un inquilino ni se le deja cambiarlo.
    expect(String(ev.fetch.mock.calls[0][0])).toContain("/public/demo/verify/");
  });

  it("un código con forma equivocada no llega a molestar al backend", async () => {
    const ev = evento("no-es-un-codigo", { ok: true, status: 200, json: async () => ({}) });
    const res = await GET(ev);
    expect(res.status).toBe(400);
    expect(ev.fetch).not.toHaveBeenCalled();
  });

  it("mantiene NEUTRO el 404 y no distingue inexistente de clasificado", async () => {
    // El backend responde igual para un código que no existe y para uno de un
    // radicado clasificado, para no confirmar su existencia. La pantalla no
    // debe deshacer esa indistinción.
    const ev = evento(CODIGO, {
      ok: false,
      status: 404,
      json: async () => ({ detail: { error: "not_found" } }),
    });
    const res = await GET(ev);
    const cuerpo = await res.json();
    expect(res.status).toBe(404);
    expect(cuerpo.detail).toMatch(/no encontramos/i);
    expect(JSON.stringify(cuerpo)).not.toMatch(/clasificad|reservad|permiso/i);
  });
});
