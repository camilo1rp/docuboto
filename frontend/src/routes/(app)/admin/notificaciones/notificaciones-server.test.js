// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * notificaciones-server.test.js — `load` de /admin/notificaciones
 * (+page.server.js). Cubre las dos facetas independientes (historial +
 * webhooks) cargadas con Promise.allSettled: cada una debe mantener su
 * propio estado de error sin tumbar la otra.
 */
import { describe, it, expect, vi } from "vitest";
import { load } from "./+page.server.js";

const parentWith =
  (tenant_slug = "icetex", sub = "admin-1") =>
  async () => ({
    user: { tenant_slug, sub },
  });

function fakeFetch({
  historyOk = true,
  webhooksOk = true,
  historyThrows = false,
  webhooksThrows = false,
} = {}) {
  return vi.fn(async (input) => {
    const url = String(input);
    if (url.includes("/notifications/history")) {
      if (historyThrows) throw new Error("network down");
      if (!historyOk)
        return { ok: false, status: 500, json: async () => ({ detail: "falla historial" }) };
      return { ok: true, status: 200, json: async () => [{ id: "n1" }] };
    }
    if (url.includes("/webhooks")) {
      if (webhooksThrows) throw new Error("network down");
      if (!webhooksOk)
        return { ok: false, status: 500, json: async () => ({ detail: "falla webhooks" }) };
      return { ok: true, status: 200, json: async () => [{ id: "wh1" }] };
    }
    throw new Error(`fetch no esperado: ${url}`);
  });
}

describe("load /admin/notificaciones", () => {
  it("carga historial y webhooks en paralelo, ambos exitosos", async () => {
    const fetch = fakeFetch();
    const result = await load({ cookies: { get: () => "tok" }, parent: parentWith(), fetch });
    expect(result.history).toEqual([{ id: "n1" }]);
    expect(result.historyError).toBeNull();
    expect(result.webhooks).toEqual([{ id: "wh1" }]);
    expect(result.webhooksError).toBeNull();
  });

  it("historyLimit fijo en 50, sin paginación fingida", async () => {
    const fetch = fakeFetch();
    const result = await load({ cookies: { get: () => "tok" }, parent: parentWith(), fetch });
    expect(result.historyLimit).toBe(50);
    const historyCall = fetch.mock.calls.find(([u]) =>
      String(u).includes("/notifications/history"),
    );
    expect(String(historyCall[0])).toContain("limit=50");
  });

  it("fallo en /notifications/history NO tumba los webhooks ya cargados", async () => {
    const fetch = fakeFetch({ historyOk: false });
    const result = await load({ cookies: { get: () => "tok" }, parent: parentWith(), fetch });
    expect(result.history).toEqual([]);
    expect(result.historyError).toBeTruthy();
    expect(result.webhooks).toEqual([{ id: "wh1" }]);
    expect(result.webhooksError).toBeNull();
  });

  it("fallo en /webhooks NO tumba el historial ya cargado", async () => {
    const fetch = fakeFetch({ webhooksOk: false });
    const result = await load({ cookies: { get: () => "tok" }, parent: parentWith(), fetch });
    expect(result.webhooks).toEqual([]);
    expect(result.webhooksError).toBeTruthy();
    expect(result.history).toEqual([{ id: "n1" }]);
    expect(result.historyError).toBeNull();
  });

  it("red caída solo en /webhooks produce webhooksError explícito, sin afectar el historial", async () => {
    const fetch = fakeFetch({ webhooksThrows: true });
    const result = await load({ cookies: { get: () => "tok" }, parent: parentWith(), fetch });
    expect(result.webhooksError).toBe("No se pudo conectar al servidor de webhooks.");
    expect(result.history).toEqual([{ id: "n1" }]);
  });

  it("propaga Authorization/X-Tenant-Slug/X-User-Id desde cookies/parent a ambas llamadas", async () => {
    const fetch = fakeFetch();
    await load({
      cookies: { get: () => "tok-xyz" },
      parent: parentWith("icetex", "admin-9"),
      fetch,
    });
    for (const [, opts] of fetch.mock.calls) {
      expect(opts.headers.Authorization).toBe("Bearer tok-xyz");
      expect(opts.headers["X-Tenant-Slug"]).toBe("icetex");
      expect(opts.headers["X-User-Id"]).toBe("admin-9");
    }
  });
});
