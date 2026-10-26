// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * colas-server.test.js — `load` de /admin/colas (+page.server.js, ADR-021
 * Fase 5). Cubre: carga de la primera página del servicio por defecto
 * (workflow, status=pending), y degradación
 * ante error/red caída.
 */
import { describe, it, expect, vi } from "vitest";
import { load } from "./+page.server.js";

const parentWith =
  (tenant_slug = "icetex", sub = "admin-1") =>
  async () => ({
    user: { tenant_slug, sub },
  });

describe("load /admin/colas", () => {
  it("consulta el servicio por defecto 'workflow' con status=pending y propaga Authorization/X-Tenant-Slug/X-User-Id", async () => {
    const fetch = vi.fn(async (input) => {
      expect(String(input)).toContain("/workflow/admin/deadletter/");
      expect(String(input)).toContain("status=pending");
      return new Response(JSON.stringify([{ event_id: "e1" }]), {
        status: 200,
        headers: { "X-Total-Count": "1" },
      });
    });
    const result = await load({
      cookies: { get: () => "tok-abc" },
      parent: parentWith("icetex", "admin-1"),
      fetch,
    });
    const [, opts] = fetch.mock.calls[0];
    expect(opts.headers.Authorization).toBe("Bearer tok-abc");
    expect(opts.headers["X-Tenant-Slug"]).toBe("icetex");
    expect(opts.headers["X-User-Id"]).toBe("admin-1");
    expect(result.defaultService).toBe("workflow");
    expect(result.items).toEqual([{ event_id: "e1" }]);
    expect(result.totalCount).toBe(1);
    expect(result.loadError).toBeNull();
  });

  it("error del gateway produce loadError y lista vacía", async () => {
    const fetch = vi.fn(async () => ({
      ok: false,
      status: 403,
      json: async () => ({ detail: "forbidden" }),
    }));
    const result = await load({ cookies: { get: () => "tok" }, parent: parentWith(), fetch });
    expect(result.items).toEqual([]);
    expect(result.loadError).toBe("forbidden");
  });

  it("red caída produce loadError explícito, no una excepción sin capturar", async () => {
    const fetch = vi.fn(async () => {
      throw new Error("network down");
    });
    const result = await load({ cookies: { get: () => "tok" }, parent: parentWith(), fetch });
    expect(result.items).toEqual([]);
    expect(result.loadError).toBe("No se pudo conectar al servidor de colas de errores.");
  });
});
