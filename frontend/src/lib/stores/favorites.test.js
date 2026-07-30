// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

/**
 * favorites.test.js — Store de accesos fijados (Sistema de Diseño §10).
 *
 * Cubre:
 *  - namespacing de la clave de storage por tenant + usuario (no hay fuga
 *    entre tenants ni entre usuarios del mismo tenant, mismo patrón que
 *    tablePersistence.test.js).
 *  - round-trip real en localStorage (jsdom) vía initFavorites/toggleFavorite.
 *  - `pickFavoriteNavItems` solo puede mostrar destinos presentes en la lista
 *    YA autorizada que recibe — la regla de seguridad central de este store.
 */
import { describe, it, expect, beforeEach } from "vitest";
import { get } from "svelte/store";
import {
  buildFavoritesKey,
  favoriteHrefs,
  initFavorites,
  toggleFavorite,
  resetFavorites,
  clearPersistedFavorites,
  pickFavoriteNavItems,
} from "./favorites.js";

describe("buildFavoritesKey — namespacing por tenant + usuario", () => {
  it("compone la clave con los dos segmentos", () => {
    expect(buildFavoritesKey("icetex", "u-1")).toBe("op-favorites:icetex:u-1");
  });

  it("cae a 'anon' cuando falta tenant o usuario", () => {
    expect(buildFavoritesKey(null, undefined)).toBe("op-favorites:anon:anon");
  });

  it("dos tenants distintos obtienen claves distintas (sin fuga entre tenants)", () => {
    expect(buildFavoritesKey("icetex", "u-1")).not.toBe(buildFavoritesKey("otro-tenant", "u-1"));
  });

  it("dos usuarios del mismo tenant obtienen claves distintas", () => {
    expect(buildFavoritesKey("icetex", "u-1")).not.toBe(buildFavoritesKey("icetex", "u-2"));
  });
});

describe("initFavorites/toggleFavorite — persistencia real en localStorage (jsdom)", () => {
  beforeEach(() => {
    window.localStorage.clear();
    resetFavorites();
  });

  it("sin favoritos previos, initFavorites deja el store vacío", () => {
    initFavorites("icetex", "u-1");
    expect(get(favoriteHrefs)).toEqual([]);
  });

  it("toggleFavorite fija un href y lo persiste bajo la clave espaciada", () => {
    initFavorites("icetex", "u-1");
    toggleFavorite("/bandeja");

    expect(get(favoriteHrefs)).toEqual(["/bandeja"]);
    const raw = window.localStorage.getItem("op-favorites:icetex:u-1");
    expect(JSON.parse(raw)).toEqual(["/bandeja"]);
  });

  it("toggleFavorite sobre un href ya fijado lo desfija", () => {
    initFavorites("icetex", "u-1");
    toggleFavorite("/bandeja");
    toggleFavorite("/bandeja");

    expect(get(favoriteHrefs)).toEqual([]);
  });

  it("un segundo initFavorites de OTRO usuario no ve los favoritos del primero (no hay fuga)", () => {
    initFavorites("icetex", "u-1");
    toggleFavorite("/bandeja");

    initFavorites("icetex", "u-2");
    expect(get(favoriteHrefs)).toEqual([]);
  });

  it("volver a initFavorites del mismo tenant+usuario recupera lo persistido", () => {
    initFavorites("icetex", "u-1");
    toggleFavorite("/bandeja");
    toggleFavorite("/expedientes");

    resetFavorites();
    expect(get(favoriteHrefs)).toEqual([]);

    initFavorites("icetex", "u-1");
    expect(get(favoriteHrefs)).toEqual(["/bandeja", "/expedientes"]);
  });

  it("clearPersistedFavorites borra tanto el store como lo persistido", () => {
    initFavorites("icetex", "u-1");
    toggleFavorite("/bandeja");

    clearPersistedFavorites();
    expect(get(favoriteHrefs)).toEqual([]);
    expect(window.localStorage.getItem("op-favorites:icetex:u-1")).toBeNull();
  });

  it("ignora entradas corruptas/no-string persistidas a mano (JSON válido, contenido inválido)", () => {
    window.localStorage.setItem("op-favorites:icetex:u-1", JSON.stringify(["/bandeja", 42, null]));
    initFavorites("icetex", "u-1");
    expect(get(favoriteHrefs)).toEqual(["/bandeja"]);
  });
});

describe("pickFavoriteNavItems — nunca expone un destino fuera de la lista autorizada", () => {
  const authorized = [
    { href: "/dashboard", label: "Panel principal" },
    { href: "/bandeja", label: "Bandeja" },
  ];

  it("intersecta hrefs fijados contra la lista autorizada, preservando su orden", () => {
    const result = pickFavoriteNavItems(authorized, ["/bandeja", "/dashboard"]);
    expect(result.map((i) => i.href)).toEqual(["/dashboard", "/bandeja"]);
  });

  it("un href fijado que YA NO está en la lista autorizada (permiso perdido) desaparece", () => {
    // Simula un usuario que perdió PERM_FIRMA entre sesiones: /firmas quedó
    // fijado en localStorage pero ya no está en la lista permission-filtrada.
    const result = pickFavoriteNavItems(authorized, ["/bandeja", "/firmas"]);
    expect(result.map((i) => i.href)).toEqual(["/bandeja"]);
    expect(result.some((i) => i.href === "/firmas")).toBe(false);
  });

  it("lista autorizada vacía (usuario sin sesión/permisos) nunca expone favoritos", () => {
    expect(pickFavoriteNavItems([], ["/bandeja", "/dashboard"])).toEqual([]);
  });
});
