// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
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
 * nada**. La ruta heredada tenía un botón que radicaba de golpe todo lo no
 * leído, y un `load` que se equivocara de método habría convertido abrir una
 * pantalla en un acto irreversible sobre la secuencia oficial de radicados.
 * Esa ruta está retirada, pero la invariante se mantiene comprobada: esta
 * pantalla es configuración, y la configuración se lee.
 */
import { describe, it, expect, vi } from "vitest";
import { load } from "./+page.server.js";

const parentWith =
  (tenant_slug = "icetex", sub = "admin-1") =>
  async () => ({ user: { tenant_slug, sub } });

const ok = (body, status = 200) => new Response(JSON.stringify(body), { status });

describe("load /admin/correo", () => {
  /**
   * Devuelve un `fetch` falso que RECOGE las llamadas en vez de comprobarlas.
   *
   * Las aserciones NO pueden ir dentro del mock: el `load` envuelve cada
   * petición en un `try/catch` que degrada cualquier fallo a un error de
   * conexión, y ahí se traga también el error que lanza un `expect`. La primera
   * versión de este test asertaba dentro, y por eso seguía en verde cuando se
   * cambiaba la URL de las cuentas por una inventada — lo destapó la
   * verificación por mutación. Se recoge y se comprueba fuera.
   */
  function fetchQueRecoge(responder) {
    const llamadas = [];
    const fake = vi.fn(async (input, opts) => {
      llamadas.push({ url: String(input), opts });
      return responder(String(input));
    });
    return { fake, llamadas };
  }

  const respuestaPorDefecto = (url) => {
    if (url.includes("/email/cuentas")) return ok([{ id: "c1", nombre: "PQRS" }]);
    if (url.includes("/email/apps-oauth")) return ok([{ id: "a1", proveedor: "gmail" }]);
    return ok({});
  };

  it("consulta las dos fuentes y no escribe nada", async () => {
    const { fake, llamadas } = fetchQueRecoge(respuestaPorDefecto);

    const result = await load({
      cookies: { get: () => "tok-abc" },
      parent: parentWith("icetex", "admin-1"),
      fetch: fake,
    });

    const urls = llamadas.map((l) => l.url);
    expect(urls.some((u) => u.endsWith("/email/cuentas"))).toBe(true);
    expect(urls.some((u) => u.endsWith("/email/apps-oauth"))).toBe(true);

    // Y NADA de la ruta heredada: ni el poll ni su estado siguen existiendo.
    expect(urls.every((u) => !u.includes("/ingest/email"))).toBe(true);
    expect(urls.every((u) => !u.includes("/poll"))).toBe(true);
    expect(llamadas.every((l) => (l.opts?.method ?? "GET") === "GET")).toBe(true);

    for (const { opts } of llamadas) {
      expect(opts.headers.Authorization).toBe("Bearer tok-abc");
      expect(opts.headers["X-Tenant-Slug"]).toBe("icetex");
      expect(opts.headers["X-User-Id"]).toBe("admin-1");
    }

    expect(result.cuentas).toHaveLength(1);
    expect(result.cuentasError).toBeNull();
    expect(result.apps).toHaveLength(1);
    expect(result.appsError).toBeNull();
  });

  it("un fallo al listar las cuentas no oculta las aplicaciones, y conserva el status", async () => {
    const { fake } = fetchQueRecoge((url) =>
      url.endsWith("/email/cuentas")
        ? ok({ error: "forbidden", detail: "Sin permiso", status: 403 }, 403)
        : respuestaPorDefecto(url),
    );
    const result = await load({
      cookies: { get: () => "t" },
      parent: parentWith(),
      fetch: fake,
    });
    expect(result.cuentas).toEqual([]);
    expect(result.cuentasError.status).toBe(403);
    // Lo demás sigue entregándose: cada faceta tiene su propio error.
    expect(result.apps).toHaveLength(1);
    expect(result.appsError).toBeNull();
  });

  it("con la red caída ambos errores se rellenan y ninguno queda como pantalla vacía", async () => {
    const fetch = vi.fn(async () => {
      throw new Error("ECONNREFUSED");
    });
    const result = await load({ cookies: { get: () => "t" }, parent: parentWith(), fetch });
    expect(result.cuentasError.status).toBe(503);
    expect(result.appsError.status).toBe(503);
    expect(result.cuentas).toEqual([]);
    expect(result.apps).toEqual([]);
  });
});
