// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/** Proxy de la cola de anulaciones: contrato y propagación del recuento. */
import { describe, expect, it, vi } from "vitest";
import { GET } from "./+server.js";

const locals = {
  accessToken: "tok",
  user: { tenant_slug: "demo", sub: "11111111-1111-1111-1111-111111111111" },
};

function evento({ respuesta, query = "" }) {
  return {
    locals,
    url: new URL(`http://x/anulaciones/api/cola${query}`),
    fetch: vi.fn().mockResolvedValue(respuesta),
  };
}

function respuestaOk(items, total) {
  return {
    ok: true,
    status: 200,
    headers: new Headers({ "X-Total-Count": String(total) }),
    json: async () => items,
  };
}

describe("/anulaciones/api/cola", () => {
  it("sin sesión responde 401 y no llama al gateway", async () => {
    const ev = evento({ respuesta: respuestaOk([], 0) });
    ev.locals = { accessToken: "" };
    const res = await GET(ev);
    expect(res.status).toBe(401);
    expect(ev.fetch).not.toHaveBeenCalled();
  });

  it("devuelve items y el total que viene en X-Total-Count", async () => {
    const items = [{ id: "a", tracking_number: "2026-DEMO-S-000012" }];
    const ev = evento({ respuesta: respuestaOk(items, 7) });
    const res = await GET(ev);
    const cuerpo = await res.json();
    expect(res.status).toBe(200);
    expect(cuerpo.items).toEqual(items);
    // El total NO se deduce de items.length: el backend lo recorta por
    // clearance igual que las filas, y la longitud de la página no es el total.
    expect(cuerpo.total).toBe(7);
  });

  it("acota la paginación en vez de reenviar lo que llegue", async () => {
    const ev = evento({ respuesta: respuestaOk([], 0), query: "?page=0&size=9999" });
    await GET(ev);
    const url = ev.fetch.mock.calls[0][0];
    expect(url).toContain("page=1");
    expect(url).toContain("size=20");
  });

  it("propaga el estado del backend en vez de convertirlo en lista vacía", async () => {
    // Un 403 presentado como «no hay nada» afirma que la entidad no tiene
    // anulaciones pendientes: es el defecto que ya apareció en /envios.
    const ev = evento({
      respuesta: {
        ok: false,
        status: 403,
        headers: new Headers(),
        json: async () => ({ error: "forbidden", detail: "Se requiere el permiso PERM_PANU_CODI" }),
      },
    });
    const res = await GET(ev);
    expect(res.status).toBe(403);
    expect((await res.json()).error).toBe("forbidden");
  });
});
