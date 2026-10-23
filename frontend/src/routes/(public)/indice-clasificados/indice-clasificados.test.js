// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * Índice público de información clasificada (C-2, ADR-039): carga SSR y descarga
 * CSV, sin sesión. Un 503 NO se presenta como índice vacío.
 */
import { describe, it, expect, vi } from "vitest";

vi.mock("$lib/utils/config.server.js", () => ({
  API_V1: "http://gw/api/v1",
  PUBLIC_TENANT: "demo",
}));

const { load } = await import("./+page.server.js");
const { GET: csvGet } = await import("./csv/+server.js");

const resp = (status, cuerpo) =>
  new Response(typeof cuerpo === "string" ? cuerpo : JSON.stringify(cuerpo), { status });

describe("load", () => {
  it("control positivo: pide el índice del tenant público sin cabeceras de sesión", async () => {
    const f = vi.fn(async () => resp(200, [{ serie_codigo: null, serie_nombre: "x" }]));
    const r = await load({ fetch: f });
    expect(f.mock.calls[0][0]).toBe("http://gw/api/v1/public/archive/indice-clasificados/demo");
    expect(f.mock.calls[0][1]).toBeUndefined();
    expect(r.filas).toHaveLength(1);
    expect(r.loadError).toBeNull();
  });
  it("503 → error explícito, nunca lista vacía presentada como completa", async () => {
    const r = await load({
      fetch: async () => resp(503, { detail: { error: "indice_no_disponible" } }),
    });
    expect(r.loadError).toMatch(/no se puede componer/i);
    expect(r.disponible).toBe(true);
  });
  it("404 → no disponible; fallo de red → error", async () => {
    expect((await load({ fetch: async () => resp(404, {}) })).disponible).toBe(false);
    const r = await load({
      fetch: async () => {
        throw new Error("red");
      },
    });
    expect(r.loadError).toBeTruthy();
  });
});

describe("csv GET", () => {
  it("control positivo: reenvía el CSV como adjunto", async () => {
    globalThis.fetch = vi.fn(async () => resp(200, "a,b\n1,2\n"));
    const res = await csvGet({ fetch: globalThis.fetch });
    expect(res.status).toBe(200);
    expect(res.headers.get("Content-Type")).toContain("text/csv");
    expect(res.headers.get("Content-Disposition")).toContain("indice-clasificados.csv");
    expect(await res.text()).toBe("a,b\n1,2\n");
  });
  it("origen caído → 503 JSON, no un CSV vacío", async () => {
    const res = await csvGet({ fetch: async () => resp(503, {}) });
    expect(res.status).toBe(503);
    expect((await res.json()).error).toBe("indice_no_disponible");
  });
});
