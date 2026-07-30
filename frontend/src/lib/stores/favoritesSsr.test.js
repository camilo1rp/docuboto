// OrpycaMCP — Sistema de Gestión Documental Electrónico de Archivo (SGDEA).
// Desarrollo original de Orpyca; adaptado a partir de Orfeo bajo licencia GPL.
// Contacto: aurigadl@gmail.com
//
// Software libre: este archivo se distribuye bajo la Licencia Pública General
// Affero de GNU (AGPL) versión 3 o, a su elección, cualquier versión posterior,
// conservando los términos de la GPL del Orfeo original del que deriva. Se entrega
// SIN GARANTÍA ALGUNA. Véase el archivo LICENSE y <https://www.gnu.org/licenses/>.

// @vitest-environment node

/**
 * favoritesSsr.test.js — Regresión de estilo D-16: `favorites.js` reutiliza
 * los helpers ya endurecidos de `tablePersistence.js`
 * (`readJson`/`writeJson`/`removeKey`), que son no-ops seguros cuando
 * `window` no existe. Este archivo corre en Node real (sin jsdom, ver el
 * mismo razonamiento en `ssrSafety.test.js`/`tablePersistenceSsr.test.js`)
 * para probar que `initFavorites`/`toggleFavorite` no revientan durante un
 * render de servidor, aunque el componente los invoque por error fuera de
 * `onMount`/un guard `browser`.
 */
import { describe, it, expect, beforeEach } from "vitest";
import { get } from "svelte/store";
import {
  favoriteHrefs,
  initFavorites,
  toggleFavorite,
  resetFavorites,
  clearPersistedFavorites,
} from "./favorites.js";

describe("favorites.js — no revienta cuando `window` no existe (SSR real)", () => {
  beforeEach(() => {
    resetFavorites();
  });

  it("`window` efectivamente no está definido en este entorno", () => {
    expect(typeof window).toBe("undefined");
  });

  it("initFavorites no lanza y deja el store en un array vacío", () => {
    expect(() => initFavorites("icetex", "u-1")).not.toThrow();
    expect(get(favoriteHrefs)).toEqual([]);
  });

  it("toggleFavorite no lanza aunque no haya `window` para persistir", () => {
    initFavorites("icetex", "u-1");
    expect(() => toggleFavorite("/bandeja")).not.toThrow();
    // El cambio en memoria sigue aplicando (SSR real no vuelve a montar el
    // componente sin recargar la página) aunque no haya podido persistirse.
    expect(get(favoriteHrefs)).toEqual(["/bandeja"]);
  });

  it("clearPersistedFavorites no lanza sin `window`", () => {
    initFavorites("icetex", "u-1");
    expect(() => clearPersistedFavorites()).not.toThrow();
  });
});
