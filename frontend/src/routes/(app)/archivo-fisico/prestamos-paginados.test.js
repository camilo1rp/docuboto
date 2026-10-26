// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * El proxy de préstamos pide una página y devuelve cuántos hay.
 *
 * `GET /prestamos` servía la tabla ENTERA sin `LIMIT`, y el proxy no pedía
 * página ni conservaba el `X-Total-Count`. Los préstamos activos y los vencidos
 * están acotados por la operación —son los que alguien tiene encima de la
 * mesa—, pero `devuelto` es el histórico: crece con cada préstamo que se cierra
 * y no deja de crecer.
 *
 * Lo que se mide aquí es el SALTO: lo que el proxy reenvía y lo que conserva de
 * vuelta. Una lista devuelta por un doble no dice nada sobre qué se pidió, y una
 * cabecera que el proxy tira es indistinguible de una que el servicio no emite.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";

import { GET as PRESTAMOS } from "./api/prestamos/+server.js";
import { GET as PRESTAMOS_DE_UNIDAD } from "./api/unidades/[id]/prestamos/+server.js";

const LOCALS = { accessToken: "t", user: { tenant_slug: "demo" } };

function peticion(qs = "") {
  return { locals: LOCALS, url: new URL(`http://app/archivo-fisico/api/prestamos${qs}`) };
}

function respuesta(items, total) {
  return {
    ok: true,
    status: 200,
    headers: { get: (h) => (h === "X-Total-Count" ? String(total) : null) },
    json: async () => items,
  };
}

describe("/archivo-fisico/api/prestamos", () => {
  beforeEach(() => {
    global.fetch = vi.fn();
  });

  it("pide una página acotada aunque el cliente no la pida", async () => {
    global.fetch.mockResolvedValueOnce(respuesta([], 0));

    await PRESTAMOS(peticion("?estado=devuelto"));

    const url = new URL(global.fetch.mock.calls[0][0]);
    expect(url.searchParams.get("page")).toBe("1");
    // 100 es el tope del backend. Sin esto, `devuelto` volvía a traer el
    // histórico entero — el defecto que se está cerrando.
    expect(url.searchParams.get("size")).toBe("100");
    expect(url.searchParams.get("estado")).toBe("devuelto");
  });

  it("reenvía la página que el cliente pide («cargar más»)", async () => {
    global.fetch.mockResolvedValueOnce(respuesta([], 0));

    await PRESTAMOS(peticion("?estado=devuelto&page=3&size=50"));

    const url = new URL(global.fetch.mock.calls[0][0]);
    expect(url.searchParams.get("page")).toBe("3");
    expect(url.searchParams.get("size")).toBe("50");
  });

  it("una página que no es un número se ignora en vez de viajar", async () => {
    // Llegaría al backend como `422` o, peor, como una consulta sin acotar.
    global.fetch.mockResolvedValueOnce(respuesta([], 0));

    await PRESTAMOS(peticion("?estado=prestado&page=; DROP&size=abc"));

    const url = new URL(global.fetch.mock.calls[0][0]);
    expect(url.searchParams.get("page")).toBe("1");
    expect(url.searchParams.get("size")).toBe("100");
  });

  it("conserva el X-Total-Count: es lo que distingue «no hay más» de «no caben»", async () => {
    global.fetch.mockResolvedValueOnce(respuesta([{ id: "p1" }], 4320));

    const res = await PRESTAMOS(peticion("?estado=devuelto"));

    expect(res.headers.get("X-Total-Count")).toBe("4320");
    expect(await res.json()).toHaveLength(1);
  });

  it("sin cabecera del servicio no inventa un total", async () => {
    // Control positivo: si el proxy fabricara el total, la pantalla anunciaría
    // una cifra que nadie calculó.
    global.fetch.mockResolvedValueOnce({
      ok: true,
      status: 200,
      headers: { get: () => null },
      json: async () => [{ id: "p1" }],
    });

    const res = await PRESTAMOS(peticion("?estado=prestado"));

    expect(res.headers.get("X-Total-Count")).toBeNull();
  });

  it("un estado inventado no viaja", async () => {
    global.fetch.mockResolvedValueOnce(respuesta([], 0));

    await PRESTAMOS(peticion("?estado=perdido"));

    const url = new URL(global.fetch.mock.calls[0][0]);
    expect(url.searchParams.get("estado")).toBeNull();
  });
});

// AREA-07-19: el listado por UNIDAD paginaba mal por omisión —no paginaba—, con el
// argumento de que una unidad no acumula miles de préstamos. Ahora el backend pagina
// (`size` <= 100) y el proxy lo pide y conserva el total.
describe("/archivo-fisico/api/unidades/[id]/prestamos", () => {
  const unidad = (qs = "") => ({
    locals: LOCALS,
    params: { id: "u-1" },
    url: new URL(`http://app/archivo-fisico/api/unidades/u-1/prestamos${qs}`),
  });

  beforeEach(() => {
    global.fetch = vi.fn();
  });

  it("pide una página acotada aunque el cliente no la pida", async () => {
    global.fetch.mockResolvedValueOnce(respuesta([], 0));

    await PRESTAMOS_DE_UNIDAD(unidad());

    const url = new URL(global.fetch.mock.calls[0][0]);
    expect(url.pathname).toBe("/api/v1/unidades/u-1/prestamos");
    expect(url.searchParams.get("page")).toBe("1");
    expect(url.searchParams.get("size")).toBe("100");
  });

  it("reenvía la página pedida y conserva el X-Total-Count", async () => {
    global.fetch.mockResolvedValueOnce(respuesta([{ id: "p" }], 25));

    const res = await PRESTAMOS_DE_UNIDAD(unidad("?page=2&size=20"));

    const url = new URL(global.fetch.mock.calls[0][0]);
    expect(url.searchParams.get("page")).toBe("2");
    expect(url.searchParams.get("size")).toBe("20");
    expect(res.headers.get("X-Total-Count")).toBe("25");
  });

  it("un valor que no es número se ignora en vez de viajar", async () => {
    global.fetch.mockResolvedValueOnce(respuesta([], 0));

    await PRESTAMOS_DE_UNIDAD(unidad("?page=x&size=; DROP"));

    const url = new URL(global.fetch.mock.calls[0][0]);
    expect(url.searchParams.get("page")).toBe("1");
    expect(url.searchParams.get("size")).toBe("100");
  });
});
