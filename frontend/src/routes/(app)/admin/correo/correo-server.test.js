// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * correo-server.test.js — `load` de /admin/correo (ingesta de correo, E19).
 *
 * La invariante que más importa aquí: **cargar la pantalla no puede radicar
 * nada**. El endpoint de estado se construyó precisamente porque el poll era la
 * única puerta, y un `load` que se equivocara de método convertiría abrir una
 * pantalla en un acto irreversible sobre la secuencia oficial de radicados.
 */
import { describe, it, expect, vi } from "vitest";
import { load } from "./+page.server.js";

const parentWith =
  (tenant_slug = "icetex", sub = "admin-1") =>
  async () => ({ user: { tenant_slug, sub } });

const ok = (body, status = 200) => new Response(JSON.stringify(body), { status });

describe("load /admin/correo", () => {
  it("consulta el estado y el historial, y NUNCA dispara el poll", async () => {
    const fetch = vi.fn(async (input, opts) => {
      const url = String(input);
      // Ninguna petición del `load` puede ser una escritura ni tocar el poll.
      expect(opts?.method ?? "GET").toBe("GET");
      expect(url).not.toContain("/poll");
      if (url.includes("/ingest/email/estado")) {
        return ok({ habilitada: true, cuenta: "correspondencia@e.gov.co", buzon: "INBOX" });
      }
      expect(url).toContain("action=ingest.email.poll");
      return ok([{ id: 9, ts: "2026-08-20T10:00:00Z", payload: { ingeridos: 3 } }]);
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
    expect(result.estado.habilitada).toBe(true);
    expect(result.estadoError).toBeNull();
    expect(result.historial).toHaveLength(1);
    expect(result.historialError).toBeNull();
  });

  it("un 403 en el estado conserva el status, para que la pantalla no lo confunda con 'apagada'", async () => {
    const fetch = vi.fn(async (input) =>
      String(input).includes("/estado")
        ? ok({ error: "forbidden", detail: "Sin permiso", status: 403 }, 403)
        : ok([]),
    );
    const result = await load({ cookies: { get: () => "t" }, parent: parentWith(), fetch });
    expect(result.estadoError.status).toBe(403);
    expect(result.estadoError.detail).toContain("Sin permiso");
    expect(result.estado).toBeNull();
  });

  it("si el historial falla, el estado se sigue entregando — la pantalla no se cae entera", async () => {
    const fetch = vi.fn(async (input) =>
      String(input).includes("/estado")
        ? ok({ habilitada: false, motivo: "imap_no_configurado" })
        : ok({ error: "boom", detail: "Auditoría no disponible", status: 500 }, 500),
    );
    const result = await load({ cookies: { get: () => "t" }, parent: parentWith(), fetch });
    expect(result.estado.motivo).toBe("imap_no_configurado");
    expect(result.historial).toEqual([]);
    expect(result.historialError.status).toBe(500);
  });

  it("con la red caída ambos errores se rellenan y ninguno queda como pantalla vacía", async () => {
    const fetch = vi.fn(async () => {
      throw new Error("ECONNREFUSED");
    });
    const result = await load({ cookies: { get: () => "t" }, parent: parentWith(), fetch });
    expect(result.estadoError.status).toBe(503);
    expect(result.historialError.status).toBe(503);
  });
});
