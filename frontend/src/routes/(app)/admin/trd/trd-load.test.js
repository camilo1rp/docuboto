// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.
/**
 * trd-load.test.js — el loader de /admin/trd pide la lista EN TRÁMITE (C-4).
 * Sin `incluir_en_tramite=true` el backend sirve solo convalidada/migrada y sin
 * acta ni autor: la pantalla dejaría de ver borradores y aprobadas.
 */
import { describe, it, expect, vi } from "vitest";
import { load } from "./+page.server.js";

const EVENT = (fetch) => ({
  cookies: { get: () => "tok" },
  parent: async () => ({ user: { tenant_slug: "icetex", sub: "u-1" } }),
  fetch,
});

describe("load /admin/trd", () => {
  it("pide /trd con incluir_en_tramite=true y las dependencias activas", async () => {
    const urls = [];
    const fetch = vi.fn(async (url) => {
      urls.push(String(url));
      if (String(url).includes("/dependencias"))
        return new Response(JSON.stringify([{ codigo: 12, nombre: "SG" }]), { status: 200 });
      return new Response("[]", { status: 200, headers: { "X-Total-Count": "0" } });
    });
    const data = await load(EVENT(fetch));
    expect(urls.some((u) => /\/trd\?incluir_en_tramite=true&page=1&size=200/.test(u))).toBe(true);
    expect(urls.some((u) => u.includes("/dependencias?activo=true"))).toBe(true);
    expect(data.dependencias).toEqual([{ codigo: 12, nombre: "SG" }]);
  });

  it("si el catálogo de dependencias falla, la pantalla sigue y lo dice (503)", async () => {
    const fetch = vi.fn(async (url) => {
      if (String(url).includes("/dependencias"))
        return new Response(
          JSON.stringify({ error: "catalogo_dependencias_no_disponible", status: 503 }),
          { status: 503 },
        );
      return new Response("[]", { status: 200 });
    });
    const data = await load(EVENT(fetch));
    expect(data.dependencias).toEqual([]);
    expect(data.dependenciasError).toMatch(/catálogo de dependencias/);
  });

  it("un 403 de la lista en trámite (sin USUA_PERM_TRD) llega como error, no como lista vacía", async () => {
    const fetch = vi.fn(async (url) => {
      if (/\/trd\?/.test(String(url)))
        return new Response(
          JSON.stringify({ error: "forbidden", detail: "Sin permiso", status: 403 }),
          { status: 403 },
        );
      return new Response("[]", { status: 200 });
    });
    const data = await load(EVENT(fetch));
    expect(data.seriesError).toBeTruthy();
  });
});
