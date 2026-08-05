// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * usuarios-server.test.js — `load` de /admin/usuarios (+page.server.js, E08,
 * Fase 5). Cubre: propagación de token/tenant/X-User-Id desde
 * cookies/parent (nunca del querystring del cliente), paginación de
 * usuarios con X-Total-Count, y degradación independiente de los catálogos
 * auxiliares (grupos/dependencias) sin tumbar el listado principal.
 */
import { describe, it, expect, vi } from "vitest";
import { load } from "./+page.server.js";

const parentWith =
  (tenant_slug = "icetex", sub = "admin-1") =>
  async () => ({
    user: { tenant_slug, sub },
  });

function fakeFetch({ usersOk = true, groupsOk = true, depsOk = true } = {}) {
  return vi.fn(async (input) => {
    const url = String(input);
    if (url.includes("/auth/users")) {
      if (!usersOk)
        return { ok: false, status: 500, json: async () => ({ detail: "falla usuarios" }) };
      return {
        ok: true,
        status: 200,
        headers: { get: (h) => (h === "X-Total-Count" ? "2" : null) },
        json: async () => [{ id: "u1" }, { id: "u2" }],
      };
    }
    if (url.includes("/auth/groups")) {
      if (!groupsOk) return { ok: false, status: 500, json: async () => ({}) };
      return { ok: true, status: 200, json: async () => [{ id: "g1" }] };
    }
    if (url.includes("/dependencias")) {
      if (!depsOk) return { ok: false, status: 500, json: async () => ({}) };
      return { ok: true, status: 200, json: async () => [{ id: "d1" }] };
    }
    throw new Error(`fetch no esperado: ${url}`);
  });
}

describe("load /admin/usuarios", () => {
  it("propaga Authorization/X-Tenant-Slug/X-User-Id desde cookies/parent, nunca desde el querystring", async () => {
    const fetch = fakeFetch();
    const result = await load({
      cookies: { get: () => "tok-abc" },
      parent: parentWith("icetex", "admin-1"),
      fetch,
      url: new URL("http://localhost/admin/usuarios"),
    });

    const [, opts] = fetch.mock.calls[0];
    expect(opts.headers.Authorization).toBe("Bearer tok-abc");
    expect(opts.headers["X-Tenant-Slug"]).toBe("icetex");
    expect(opts.headers["X-User-Id"]).toBe("admin-1");
    expect(result.users).toHaveLength(2);
    expect(result.totalCount).toBe(2);
    expect(result.loadError).toBeNull();
  });

  it("page inválido en el querystring cae a 1 (no se reenvía crudo)", async () => {
    const fetch = fakeFetch();
    await load({
      cookies: { get: () => "tok" },
      parent: parentWith(),
      fetch,
      url: new URL("http://localhost/admin/usuarios?page=-5"),
    });
    const [calledUrl] = fetch.mock.calls[0];
    expect(String(calledUrl)).toContain("page=1");
  });

  it("fallo en /auth/users produce loadError y lista vacía", async () => {
    const fetch = fakeFetch({ usersOk: false });
    const result = await load({
      cookies: { get: () => "tok" },
      parent: parentWith(),
      fetch,
      url: new URL("http://localhost/admin/usuarios"),
    });
    expect(result.users).toEqual([]);
    expect(result.loadError).toBeTruthy();
  });

  it("fallo en /auth/groups NO tumba el listado de usuarios (degradación independiente)", async () => {
    const fetch = fakeFetch({ groupsOk: false });
    const result = await load({
      cookies: { get: () => "tok" },
      parent: parentWith(),
      fetch,
      url: new URL("http://localhost/admin/usuarios"),
    });
    expect(result.users).toHaveLength(2);
    expect(result.loadError).toBeNull();
    expect(result.groups).toEqual([]);
  });

  it("fallo en /dependencias NO tumba el listado de usuarios ni los grupos", async () => {
    const fetch = fakeFetch({ depsOk: false });
    const result = await load({
      cookies: { get: () => "tok" },
      parent: parentWith(),
      fetch,
      url: new URL("http://localhost/admin/usuarios"),
    });
    expect(result.users).toHaveLength(2);
    expect(result.groups).toHaveLength(1);
    expect(result.dependencias).toEqual([]);
  });

  it("red caída en /auth/users produce loadError explícito, no una excepción sin capturar", async () => {
    const fetch = vi.fn(async () => {
      throw new Error("network down");
    });
    const result = await load({
      cookies: { get: () => "tok" },
      parent: parentWith(),
      fetch,
      url: new URL("http://localhost/admin/usuarios"),
    });
    expect(result.loadError).toBe("No se pudo conectar al servidor de usuarios.");
  });
});

// ---------------------------------------------------------------------------
// ADR-024: solo un ROOT puede crear otro ROOT. El `load` resuelve si el propio
// llamante lo es, para que la vista no ofrezca la casilla "Otorgar ROOT" a
// quien el backend va a rechazar con 403.
// ---------------------------------------------------------------------------
describe("load — callerIsRoot (ADR-024)", () => {
  /** Ojo al orden: `/auth/users/{id}/permissions` contiene `/auth/users`, así
   *  que la rama de permisos debe comprobarse ANTES que la del listado. */
  function fetchWithPermissions({ isRoot = false, permsOk = true } = {}) {
    return vi.fn(async (input) => {
      const url = String(input);
      if (url.includes("/permissions")) {
        if (!permsOk) return { ok: false, status: 500, json: async () => ({}) };
        return { ok: true, status: 200, json: async () => ({ is_root: isRoot, permissions: {} }) };
      }
      if (url.includes("/auth/users")) {
        return {
          ok: true,
          status: 200,
          headers: { get: () => "0" },
          json: async () => [],
        };
      }
      return { ok: true, status: 200, json: async () => [] };
    });
  }

  const run = (fetch) =>
    load({
      cookies: { get: () => "tok" },
      parent: parentWith(),
      fetch,
      url: new URL("http://localhost/admin/usuarios"),
    });

  it("llamante ROOT: callerIsRoot true", async () => {
    const result = await run(fetchWithPermissions({ isRoot: true }));
    expect(result.callerIsRoot).toBe(true);
  });

  it("llamante no ROOT: callerIsRoot false", async () => {
    const result = await run(fetchWithPermissions({ isRoot: false }));
    expect(result.callerIsRoot).toBe(false);
  });

  // Fail-closed: si no se puede resolver, se asume que NO es ROOT. Un ROOT que
  // se quede sin la casilla tiene la vía de ops (`app.ops.grant_root`);
  // mostrarla por defecto devolvería el control muerto que este cambio elimina.
  it("si la consulta de permisos falla, callerIsRoot es false (fail-closed)", async () => {
    const result = await run(fetchWithPermissions({ permsOk: false }));
    expect(result.callerIsRoot).toBe(false);
  });

  it("el id del llamante va escapado en la URL de permisos", async () => {
    const fetch = fetchWithPermissions();
    await load({
      cookies: { get: () => "tok" },
      parent: async () => ({ user: { tenant_slug: "icetex", sub: "a b/c" } }),
      fetch,
      url: new URL("http://localhost/admin/usuarios"),
    });
    const permsCall = fetch.mock.calls
      .map((c) => String(c[0]))
      .find((u) => u.includes("/permissions"));
    expect(permsCall).toContain("a%20b%2Fc");
  });
});
