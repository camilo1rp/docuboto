// Docuboto — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * listaCompleta.test.js — que «completa» signifique completa, y que cuando no lo
 * sea se diga.
 *
 * El defecto que esto cierra: ocho loaders pedían `?size=200` creyendo que era
 * holgura. Es el tope del backend. A partir de la fila 201 la vista se quedaba
 * corta en silencio, con el `X-Total-Count` delante sin que nadie lo mirara.
 */
import { describe, it, expect, vi } from "vitest";

import { traerListaCompleta } from "./listaCompleta.server.js";

const mensaje = (cuerpo, porDefecto) => cuerpo?.detail ?? porDefecto;

/** Sirve `total` elementos en páginas de 200, como hace el gateway. */
function servidorCon(total, { totalCountPorPagina = null } = {}) {
  const urls = [];
  const fetchFn = vi.fn(async (url) => {
    urls.push(url);
    const u = new URL(url, "http://gw");
    const page = Number(u.searchParams.get("page"));
    const size = Number(u.searchParams.get("size"));
    const desde = (page - 1) * size;
    const lote = Array.from({ length: Math.max(0, Math.min(size, total - desde)) }, (_, i) => ({
      id: desde + i,
    }));
    const anunciado = totalCountPorPagina ?? String(total);
    return {
      ok: true,
      status: 200,
      headers: { get: (h) => (h === "X-Total-Count" ? anunciado : null) },
      json: async () => lote,
    };
  });
  return { fetchFn, urls };
}

describe("traerListaCompleta", () => {
  it("una lista que cabe en una página se trae de una sola petición", async () => {
    const { fetchFn } = servidorCon(37);

    const r = await traerListaCompleta(fetchFn, "/trd", {}, mensaje);

    expect(r.items).toHaveLength(37);
    expect(r.incompleta).toBe(false);
    expect(fetchFn).toHaveBeenCalledTimes(1);
  });

  it("EL CASO: 250 series ya no se quedan en 200", async () => {
    // Con `size=200` a secas, esta vista perdía 50 series — y como el árbol se
    // arma por `parent_id`, las subseries de las que faltaran no aparecían en
    // ninguna rama: el árbol no se veía incompleto, se veía distinto.
    const { fetchFn } = servidorCon(250);

    const r = await traerListaCompleta(fetchFn, "/trd", {}, mensaje);

    expect(r.items).toHaveLength(250);
    expect(r.total).toBe(250);
    expect(r.incompleta).toBe(false);
    expect(fetchFn).toHaveBeenCalledTimes(2);
  });

  it("respeta los filtros que ya trae la URL", async () => {
    const { fetchFn, urls } = servidorCon(10);

    await traerListaCompleta(fetchFn, "/trd?is_active=true", {}, mensaje);

    expect(urls[0]).toContain("is_active=true");
    expect(urls[0]).toContain("page=1");
    expect(urls[0]).toContain("size=200");
  });

  it("por encima del tope de páginas se DECLARA incompleta, no se finge", async () => {
    // 25 páginas × 200 = 5.000. Un tope sin aviso solo mueve el mismo defecto
    // más lejos: lo que no puede pasar es devolver una lista corta con cara de
    // completa.
    const { fetchFn } = servidorCon(6000);

    const r = await traerListaCompleta(fetchFn, "/trd", {}, mensaje);

    expect(r.items).toHaveLength(5000);
    expect(r.total).toBe(6000);
    expect(r.incompleta).toBe(true);
  });

  it("no entra en bucle si el servidor no emite X-Total-Count", async () => {
    // Un bucle que confiara solo en el total no terminaría nunca; el criterio de
    // parada es el TAMAÑO DEL LOTE.
    const { fetchFn } = servidorCon(250, { totalCountPorPagina: null });
    const sinCabecera = vi.fn(async (url) => {
      const res = await fetchFn(url);
      return { ...res, headers: { get: () => null } };
    });

    const r = await traerListaCompleta(sinCabecera, "/trd", {}, mensaje);

    expect(r.items).toHaveLength(250);
    expect(r.incompleta).toBe(false);
  });

  it("un fallo en la primera página informa del error y no inventa una lista", async () => {
    const fetchFn = vi.fn(async () => ({
      ok: false,
      status: 503,
      headers: { get: () => null },
      json: async () => ({ detail: "TRD no disponible" }),
    }));

    const r = await traerListaCompleta(fetchFn, "/trd", {}, mensaje);

    expect(r.items).toEqual([]);
    expect(r.error).toBe("TRD no disponible");
    expect(r.incompleta).toBe(true);
  });

  it("un fallo a mitad conserva lo traído y lo marca incompleto", async () => {
    // Lo ya cargado sigue sirviendo; lo que no puede es pasar por completo.
    let llamada = 0;
    const fetchFn = vi.fn(async (url) => {
      llamada += 1;
      if (llamada === 2) throw new Error("se cayó la red");
      const u = new URL(url, "http://gw");
      const size = Number(u.searchParams.get("size"));
      return {
        ok: true,
        status: 200,
        headers: { get: (h) => (h === "X-Total-Count" ? "500" : null) },
        json: async () => Array.from({ length: size }, (_, i) => ({ id: i })),
      };
    });

    const r = await traerListaCompleta(fetchFn, "/trd", {}, mensaje);

    expect(r.items).toHaveLength(200);
    expect(r.incompleta).toBe(true);
    expect(r.error).toBeNull(); // no es un fallo de carga: es una carga parcial
  });
});
