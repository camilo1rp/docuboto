// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/** Ventanilla pública: 404 `not_found` de la entidad (ADR-035), sin distinguir casos. */
import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/svelte";

vi.mock("$lib/utils/config.server.js", () => ({
  API_V1: "http://gw/api/v1",
  PUBLIC_TENANT: "demo",
}));

const { load } = await import("./radicacion/+page.server.js");
const { load: loadElim } = await import("./eliminacion/+page.server.js");
const { GET: consultaGet } = await import("./consulta/api/+server.js");
const { POST: radicarPost } = await import("./radicacion/api/+server.js");
const { default: RadicacionPage } = await import("./radicacion/+page.svelte");
const { default: EliminacionPage } = await import("./eliminacion/+page.svelte");

const r404 = () => ({
  ok: false,
  status: 404,
  json: async () => ({ detail: { error: "not_found" } }),
});

describe("ventanilla con 404 de entidad", () => {
  it("/radicacion load marca sinVentanilla y no habilitada", async () => {
    const r = await load({ fetch: vi.fn(async () => r404()) });
    expect(r.habilitada).toBe(false);
    expect(r.sinVentanilla).toBe(true);
  });

  it("/radicacion pinta «no tiene ventanilla disponible»", () => {
    render(RadicacionPage, {
      data: { tipos: [], habilitada: false, sinVentanilla: true, politica: {} },
    });
    expect(screen.getByText(/no tiene ventanilla disponible/i)).toBeTruthy();
  });

  it("/eliminacion load: 404 es «no disponible», no error", async () => {
    const r = await loadElim({ fetch: vi.fn(async () => r404()) });
    expect(r).toEqual({ disponible: false, lotes: [], loadError: null });
  });

  it("/eliminacion pinta el mismo texto", () => {
    render(EliminacionPage, { data: { disponible: false, lotes: [], loadError: null } });
    expect(screen.getByText(/no tiene portal público disponible/i)).toBeTruthy();
  });

  it("POST /radicacion/api traduce el 404 a un texto único", async () => {
    const fetch = vi.fn(async () => r404());
    const res = await radicarPost({
      request: { formData: async () => new FormData(), headers: new Headers() },
      fetch,
    });
    expect(res.status).toBe(404);
    expect((await res.json()).detail).toMatch(/no tiene ventanilla disponible/i);
  });

  it("GET /consulta/api: el 404 sigue neutro", async () => {
    const fetch = vi.fn(async () => r404());
    const res = await consultaGet({
      url: new URL(`http://x/consulta/api?codigo=${"a".repeat(32)}`),
      fetch,
    });
    expect(res.status).toBe(404);
    expect((await res.json()).detail).not.toMatch(/ventanilla/i);
  });

  it("POST /radicacion/api: 503 canal_no_disponible (entidad suspendida) tiene mensaje propio", async () => {
    const fetch = vi.fn(async () => ({
      ok: false,
      status: 503,
      json: async () => ({ detail: { error: "canal_no_disponible", detail: "x" } }),
    }));
    const res = await radicarPost({
      request: { formData: async () => new FormData(), headers: new Headers() },
      fetch,
    });
    expect(res.status).toBe(503);
    expect((await res.json()).detail).toMatch(/suspendida temporalmente la recepción/i);
  });

  it.each([
    ["plano", { error: "canal_no_disponible" }],
    ["envuelto", { detail: { error: "canal_no_disponible" } }],
  ])("/radicacion load: 503 canal_no_disponible (%s) marca canalSuspendido", async (_n, cuerpo) => {
    const fetch = vi.fn(async () => ({ ok: false, status: 503, json: async () => cuerpo }));
    const r = await load({ fetch });
    expect(r.canalSuspendido).toBe(true);
    expect(r.habilitada).toBe(false);
    expect(r.sinVentanilla).toBeUndefined();
  });

  it("/radicacion pinta la rama de recepción suspendida con enlace a /consulta", () => {
    render(RadicacionPage, {
      data: { tipos: [], habilitada: false, canalSuspendido: true, politica: {} },
    });
    expect(screen.getByRole("status").textContent).toMatch(/suspendida temporalmente la recepción/);
    expect(screen.getByRole("link", { name: "Consultar mi trámite" }).getAttribute("href")).toBe(
      "/consulta",
    );
  });
});
