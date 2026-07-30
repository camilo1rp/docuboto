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
 * tablePersistenceSsr.test.js — Regresión de estilo D-16: comprueba en un
 * entorno SIN `window` (Node real, no jsdom — ver el mismo razonamiento en
 * `ssrSafety.test.js`) que `tablePersistence.js` no lanza al ejecutarse
 * durante un render de servidor.
 */
import { describe, it, expect } from "vitest";
import { readJson, writeJson, removeKey, buildStorageKey } from "./tablePersistence.js";

describe("tablePersistence — no revienta cuando `window` no existe (SSR real)", () => {
  it("`window` efectivamente no está definido en este entorno", () => {
    expect(typeof window).toBe("undefined");
  });

  it("readJson devuelve null sin lanzar", () => {
    const key = buildStorageKey({
      tenant: "icetex",
      user: "u-1",
      tableId: "bandeja",
      kind: "columns",
    });
    expect(() => readJson(key)).not.toThrow();
    expect(readJson(key)).toBeNull();
  });

  it("writeJson es un no-op silencioso que devuelve false", () => {
    const key = buildStorageKey({
      tenant: "icetex",
      user: "u-1",
      tableId: "bandeja",
      kind: "views",
    });
    expect(() => writeJson(key, ["x"])).not.toThrow();
    expect(writeJson(key, ["x"])).toBe(false);
  });

  it("removeKey no lanza", () => {
    const key = buildStorageKey({
      tenant: "icetex",
      user: "u-1",
      tableId: "bandeja",
      kind: "columns",
    });
    expect(() => removeKey(key)).not.toThrow();
  });
});
