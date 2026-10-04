// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * pinar-puertas-restantes-server.test.js — Las tres puertas que faltaban
 * (AREA-12-03): editar el plan, historial de seguimiento y mapa de ruta.
 */
import { describe, it, expect, vi } from "vitest";

import { PATCH as planPatch } from "./api/planes/[id]/+server.js";
import { GET as mapaGet } from "./api/planes/[id]/mapa-ruta/+server.js";
import { GET as seguimientoGet } from "./api/proyectos/[proyectoId]/seguimiento/+server.js";

const ID = "11111111-1111-4111-8111-111111111111";
const locals = { accessToken: "tok", user: { tenant_slug: "demo", sub: "u-1" } };

const respuesta = (cuerpo, status = 200) =>
  new Response(JSON.stringify(cuerpo), { status, headers: { "content-type": "application/json" } });

describe("PATCH /planes/[id]", () => {
  it("sin sesión → 401 sin leer el body", async () => {
    const res = await planPatch({
      locals: {},
      params: { id: ID },
      request: { json: () => Promise.reject(new Error("no debía leerse")) },
      fetch: vi.fn(),
    });
    expect(res.status).toBe(401);
  });

  it("recorta a la allowlist: acto_administrativo y estado no viajan", async () => {
    const fetch = vi.fn(async () => respuesta({ id: ID }));
    const res = await planPatch({
      locals,
      params: { id: ID },
      request: new Request("http://x", {
        method: "PATCH",
        body: JSON.stringify({
          vision: "Nueva",
          estado: "aprobado",
          acto_administrativo: "Res. 1",
        }),
      }),
      fetch,
    });
    expect(res.status).toBe(200);
    const [url, init] = fetch.mock.calls[0];
    expect(url).toMatch(new RegExp(`/pinar/planes/${ID}$`));
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(init.body)).toEqual({ vision: "Nueva" });
  });

  it("id que no es UUID → 400 sin llamar al backend", async () => {
    const fetch = vi.fn();
    const res = await planPatch({
      locals,
      params: { id: "../x" },
      request: new Request("http://x", { method: "PATCH", body: "{}" }),
      fetch,
    });
    expect(res.status).toBe(400);
    expect(fetch).not.toHaveBeenCalled();
  });

  it("cuerpo vacío o fecha mal formada → 400", async () => {
    const mk = (b) =>
      planPatch({
        locals,
        params: { id: ID },
        request: new Request("http://x", { method: "PATCH", body: JSON.stringify(b) }),
        fetch: vi.fn(),
      });
    expect((await mk({})).status).toBe(400);
    expect((await mk({ vigencia_desde: "01/02/2026" })).status).toBe(400);
  });

  it("reenvía el 409 plan_no_editable con su cuerpo", async () => {
    const fetch = vi.fn(async () => respuesta({ error: "plan_no_editable" }, 409));
    const res = await planPatch({
      locals,
      params: { id: ID },
      request: new Request("http://x", { method: "PATCH", body: JSON.stringify({ vision: "v" }) }),
      fetch,
    });
    expect(res.status).toBe(409);
    expect((await res.json()).error).toBe("plan_no_editable");
  });
});

describe.each([
  ["GET /planes/[id]/mapa-ruta", mapaGet, { id: ID }, `/pinar/planes/${ID}/mapa-ruta`],
  [
    "GET /proyectos/[id]/seguimiento",
    seguimientoGet,
    { proyectoId: ID },
    `/pinar/proyectos/${ID}/seguimiento`,
  ],
])("%s", (_n, handler, params, esperado) => {
  it("sin sesión → 401", async () => {
    expect((await handler({ locals: {}, params, fetch: vi.fn() })).status).toBe(401);
  });

  it("reenvía con Bearer y tenant al endpoint correcto", async () => {
    const fetch = vi.fn(async () => respuesta([{ a: 1 }]));
    const res = await handler({ locals, params, fetch });
    expect(res.status).toBe(200);
    const [url, init] = fetch.mock.calls[0];
    expect(url.endsWith(esperado)).toBe(true);
    expect(init.headers.Authorization).toBe("Bearer tok");
    expect(init.headers["X-Tenant-Slug"]).toBe("demo");
  });

  it("identificador inválido → 400 sin red", async () => {
    const fetch = vi.fn();
    const clave = Object.keys(params)[0];
    expect((await handler({ locals, params: { [clave]: "nope" }, fetch })).status).toBe(400);
    expect(fetch).not.toHaveBeenCalled();
  });
});
